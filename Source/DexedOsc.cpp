/**
 *
 * Copyright (c) 2026 Atelier du Verdier
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation,
 * Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
 *
 */

#include "DexedOsc.h"
#include "PluginProcessor.h"
#include "PluginParam.h"
#include "PluginData.h"

namespace {
    const char *kReplyAddress = "/dexed/reply";
    const int kPortRange = 16;
    const int kVoiceSize = 156;          // 155 DX7 bytes + packed op switch
    const int kMaxRenderMs = 30000;

    // --- small helpers to read OSC arguments safely --------------------------
    bool argInt(const OSCMessage &m, int i, int &out) {
        if (i >= m.size()) return false;
        if (m[i].isInt32())   { out = m[i].getInt32(); return true; }
        if (m[i].isFloat32()) { out = roundToInt(m[i].getFloat32()); return true; }
        return false;
    }

    bool argFloat(const OSCMessage &m, int i, float &out) {
        if (i >= m.size()) return false;
        if (m[i].isFloat32()) { out = m[i].getFloat32(); return true; }
        if (m[i].isInt32())   { out = (float) m[i].getInt32(); return true; }
        return false;
    }

    bool argString(const OSCMessage &m, int i, String &out) {
        if (i >= m.size() || ! m[i].isString()) return false;
        out = m[i].getString();
        return true;
    }

    var ok() {
        DynamicObject::Ptr o = new DynamicObject();
        o->setProperty("ok", true);
        return var(o.get());
    }

    var error(const String &msg) {
        DynamicObject::Ptr o = new DynamicObject();
        o->setProperty("ok", false);
        o->setProperty("error", msg);
        return var(o.get());
    }

    bool isError(const var &v) {
        return v.isObject() && v.hasProperty("ok") && ! (bool) v["ok"];
    }

    /** Only absolute paths are accepted: relative ones would depend on the host's cwd. */
    bool resolvePath(const String &raw, File &out) {
        if (! File::isAbsolutePath(raw))
            return false;
        out = File(raw);
        return true;
    }
}

//==============================================================================
DexedOscServer::DexedOscServer(DexedAudioProcessor &owner) : proc(owner) {
    // index the DX7 controllers by their data offset
    for (auto *c : proc.ctrl) {
        if (auto *dx = dynamic_cast<CtrlDX*>(c)) {
            if (dx->getOffset() >= 0)
                ctrlByOffset[dx->getOffset()] = dx;
        }
    }

    if (SystemStats::getEnvironmentVariable("DEXED_OSC", "1") == "0")
        return;

    int base = SystemStats::getEnvironmentVariable("DEXED_OSC_PORT", "9000").getIntValue();
    if (base <= 0 || base > 65535 - kPortRange)
        base = 9000;

    // JUCE enables SO_REUSEADDR on datagram sockets: on Linux several instances
    // would then silently share the same UDP port. Disable it so that a busy
    // port makes bindToPort() fail and the next one is tried.
    socket.setEnablePortReuse(false);

    for (int p = base; p < base + kPortRange; p++) {
        if (socket.bindToPort(p, "127.0.0.1")) {
            if (receiver.connectToSocket(socket)) {
                receiver.addListener(this);
                port = p;
                TRACE("OSC server listening on 127.0.0.1:%d", p);
            }
            break;
        }
    }
}

DexedOscServer::~DexedOscServer() {
    *alive = false;
    receiver.removeListener(this);
    receiver.disconnect();
    socket.shutdown();
}

//==============================================================================
void DexedOscServer::reply(int replyPort, const String &address, const var &payload) {
    if (replyPort <= 0 || replyPort > 65535)
        return;
    if (! sender.connect("127.0.0.1", replyPort))
        return;
    sender.send(kReplyAddress, address, JSON::toString(payload, true));
}

void DexedOscServer::replyError(int replyPort, const String &address, const String &msg) {
    reply(replyPort, address, error(msg));
}

void DexedOscServer::markDirty() {
    // the editor polls this flag and refreshes every component
    proc.forceRefreshUI = true;
    proc.updateHostDisplay();
}

void DexedOscServer::oscMessageReceived(const OSCMessage &msg) {
    const String address = msg.getAddressPattern().toString();

    int replyPort = 0;
    if (! argInt(msg, 0, replyPort)) {
        // no reply port: nothing can be reported back, ignore malformed packets
        return;
    }

    var result;
    if      (address == "/dexed/ping")      result = cmdPing();
    else if (address == "/dexed/get")       result = cmdGet();
    else if (address == "/dexed/params")    result = cmdParams();
    else if (address == "/dexed/set")       result = cmdSet(msg);
    else if (address == "/dexed/set_param") result = cmdSetParam(msg);
    else if (address == "/dexed/voice")     result = cmdVoice(msg);
    else if (address == "/dexed/program")   result = cmdProgram(msg);
    else if (address == "/dexed/load_cart") result = cmdLoadCart(msg);
    else if (address == "/dexed/store")     result = cmdStore(msg);
    else if (address == "/dexed/save_cart") result = cmdSaveCart(msg);
    else if (address == "/dexed/note")      result = cmdNote(msg);
    else if (address == "/dexed/panic")     result = cmdPanic();
    else if (address == "/dexed/render")    result = cmdRender(msg);
    else                                    result = error("unknown address " + address);

    reply(replyPort, address, result);
}

//==============================================================================
var DexedOscServer::cmdPing() {
    DynamicObject::Ptr o = new DynamicObject();
    o->setProperty("ok", true);
    o->setProperty("app", "dexed");
    o->setProperty("version", JucePlugin_VersionString);
    o->setProperty("port", port);
    o->setProperty("wrapper", AudioProcessor::getWrapperTypeDescription(proc.wrapperType));
    o->setProperty("program", proc.getCurrentProgram());
    o->setProperty("name", Cartridge::normalizePgmName((const char *) proc.data + 145).trimEnd());
    o->setProperty("sampleRate", proc.getSampleRate());
    return var(o.get());
}

var DexedOscServer::cmdGet() {
    DynamicObject::Ptr o = new DynamicObject();
    o->setProperty("ok", true);
    o->setProperty("program", proc.getCurrentProgram());
    o->setProperty("name", Cartridge::normalizePgmName((const char *) proc.data + 145).trimEnd());
    o->setProperty("cartFile", proc.activeFileCartridge.getFullPathName());
    o->setProperty("engine", proc.getEngineType());
    o->setProperty("mono", proc.isMonoMode());

    // keep the packed op switch byte up to date (bit 5 = OP1 ... bit 0 = OP6)
    int opSwitch = 0;
    for (int i = 0; i < 6; i++)
        if (proc.controllers.opSwitch[i] == '1')
            opSwitch |= (1 << i);

    Array<var> voice;
    for (int i = 0; i < 155; i++)
        voice.add((int) proc.data[i]);
    voice.add(opSwitch);
    o->setProperty("voice", voice);

    Array<var> names;
    for (int i = 0; i < 32; i++)
        names.add(proc.currentCart.getProgramName(i).trimEnd());
    o->setProperty("programNames", names);

    // the non-DX7 controls, as host values 0..1 and display strings
    DynamicObject::Ptr extra = new DynamicObject();
    for (auto *c : { (Ctrl*) proc.fxCutoff.get(), (Ctrl*) proc.fxReso.get(),
                     (Ctrl*) proc.output.get(), (Ctrl*) proc.tune.get() }) {
        DynamicObject::Ptr e = new DynamicObject();
        e->setProperty("idx", c->idx);
        e->setProperty("host", c->getValueHost());
        e->setProperty("display", c->getValueDisplay());
        extra->setProperty(c->label, var(e.get()));
    }
    o->setProperty("extra", var(extra.get()));
    return var(o.get());
}

var DexedOscServer::cmdParams() {
    Array<var> list;
    for (auto *c : proc.ctrl) {
        DynamicObject::Ptr e = new DynamicObject();
        e->setProperty("idx", c->idx);
        e->setProperty("label", c->label);
        e->setProperty("host", c->getValueHost());
        e->setProperty("display", c->getValueDisplay());
        if (auto *dx = dynamic_cast<CtrlDX*>(c)) {
            e->setProperty("offset", dx->getOffset());
            e->setProperty("max", dx->getSteps());
            e->setProperty("value", dx->getValue());
        }
        list.add(var(e.get()));
    }
    DynamicObject::Ptr o = new DynamicObject();
    o->setProperty("ok", true);
    o->setProperty("params", list);
    return var(o.get());
}

/** /dexed/set <reply> <offset> <value> [<offset> <value> ...]  (DX7 data offsets 0..154) */
var DexedOscServer::cmdSet(const OSCMessage &msg) {
    if (msg.size() < 3 || (msg.size() - 1) % 2 != 0)
        return error("expected pairs of <offset> <value>");

    Array<var> applied;
    for (int i = 1; i + 1 < msg.size(); i += 2) {
        int offset, value;
        if (! argInt(msg, i, offset) || ! argInt(msg, i + 1, value))
            return error("arguments must be numbers");
        if (offset < 0 || offset > 154)
            return error("offset out of range 0..154: " + String(offset));

        auto it = ctrlByOffset.find(offset);
        if (it != ctrlByOffset.end()) {
            CtrlDX *dx = it->second;
            value = jlimit(0, dx->getSteps(), value);
            // goes through the host so automation/undo see the change
            dx->publishValue((float) value);
        } else if (offset >= 145) {
            // voice name characters (no controller), printable ASCII only
            proc.data[offset] = (uint8) jlimit(32, 126, value);
        } else {
            return error("no controller for offset " + String(offset));
        }

        DynamicObject::Ptr e = new DynamicObject();
        e->setProperty("offset", offset);
        e->setProperty("value", (int) proc.data[offset]);
        applied.add(var(e.get()));
    }

    markDirty();
    DynamicObject::Ptr o = new DynamicObject();
    o->setProperty("ok", true);
    o->setProperty("applied", applied);
    return var(o.get());
}

/** /dexed/set_param <reply> <ctrl idx> <host value 0..1>  (any controller, e.g. cutoff, op switch) */
var DexedOscServer::cmdSetParam(const OSCMessage &msg) {
    int idx;
    float value;
    if (! argInt(msg, 1, idx) || ! argFloat(msg, 2, value))
        return error("expected <idx> <value 0..1>");
    if (idx < 0 || idx >= proc.ctrl.size())
        return error("idx out of range");

    Ctrl *c = proc.ctrl[idx];
    c->Ctrl::publishValue(jlimit(0.0f, 1.0f, value));
    markDirty();

    DynamicObject::Ptr o = new DynamicObject();
    o->setProperty("ok", true);
    o->setProperty("label", c->label);
    o->setProperty("display", c->getValueDisplay());
    return var(o.get());
}

/** /dexed/voice <reply> <blob 155 or 156 bytes>  replace the whole current voice */
var DexedOscServer::cmdVoice(const OSCMessage &msg) {
    if (msg.size() < 2 || ! msg[1].isBlob())
        return error("expected a blob of 155 or 156 bytes");

    const MemoryBlock &blob = msg[1].getBlob();
    if (blob.getSize() != 155 && blob.getSize() != kVoiceSize)
        return error("voice blob must be 155 or 156 bytes, got " + String((int) blob.getSize()));

    uint8_t raw[kVoiceSize];
    memcpy(raw, blob.getData(), 155);
    for (int i = 0; i < 155; i++)
        if (raw[i] > 127)
            return error("byte " + String(i) + " is above 127");
    raw[155] = sysexChecksum(raw, 155);

    proc.updateProgramFromSysex(raw);

    // optional op switch byte (bit 5 = OP1 ... bit 0 = OP6, as in /dexed/get)
    if (blob.getSize() == kVoiceSize) {
        const uint8 sw = ((const uint8 *) blob.getData())[155];
        for (int op = 0; op < 6; op++)
            proc.controllers.opSwitch[op] = ((sw >> op) & 1) ? '1' : '0';
        proc.setDxValue(155, 0);   // repacks the switch and refreshes the voice
    }

    markDirty();
    return ok();
}

var DexedOscServer::cmdProgram(const OSCMessage &msg) {
    int idx;
    if (! argInt(msg, 1, idx) || idx < 0 || idx > 31)
        return error("expected program 0..31");
    proc.setCurrentProgram(idx);
    markDirty();
    return cmdPing();
}

/** /dexed/load_cart <reply> <absolute path> [program] */
var DexedOscServer::cmdLoadCart(const OSCMessage &msg) {
    String raw;
    File f;
    if (! argString(msg, 1, raw) || ! resolvePath(raw, f))
        return error("expected an absolute path to a .syx file");
    if (! f.existsAsFile())
        return error("file not found: " + raw);

    Cartridge cart;
    int rc = cart.load(f);
    if (rc != 0)
        return error(rc < 0 ? "unable to read file" : "not a valid DX7 32-voice cartridge");

    int program = 0;
    argInt(msg, 2, program);

    proc.loadCartridge(cart);
    proc.activeFileCartridge = f;
    proc.setCurrentProgram(jlimit(0, 31, program));
    markDirty();
    return cmdGet();
}

/** /dexed/store <reply> <program 0..31> [name]  store the current voice in the cartridge (in memory) */
var DexedOscServer::cmdStore(const OSCMessage &msg) {
    int idx;
    if (! argInt(msg, 1, idx) || idx < 0 || idx > 31)
        return error("expected program 0..31");

    String name;
    if (! argString(msg, 2, name))
        name = Cartridge::normalizePgmName((const char *) proc.data + 145);
    name = name.substring(0, 10);

    proc.currentCart.packProgram((uint8_t *) proc.data, idx, name, proc.controllers.opSwitch);
    proc.currentCart.getProgramNames(proc.programNames);
    proc.setCurrentProgram(idx);
    markDirty();
    return ok();
}

/** /dexed/save_cart <reply> <absolute path>  write the current cartridge to a .syx file */
var DexedOscServer::cmdSaveCart(const OSCMessage &msg) {
    String raw;
    File f;
    if (! argString(msg, 1, raw) || ! resolvePath(raw, f))
        return error("expected an absolute path");
    if (! f.hasFileExtension("syx"))
        return error("the file name must end with .syx");
    if (! f.getParentDirectory().isDirectory())
        return error("directory does not exist: " + f.getParentDirectory().getFullPathName());

    uint8_t sysex[SYSEX_SIZE];
    proc.currentCart.saveVoice(sysex);
    if (! f.replaceWithData(sysex, SYSEX_SIZE))
        return error("unable to write " + raw);

    proc.activeFileCartridge = f;
    markDirty();
    return ok();
}

/** /dexed/note <reply> <note 0..127> [velocity 1..127 = 100] [duration ms = 500] */
var DexedOscServer::cmdNote(const OSCMessage &msg) {
    int note, velocity = 100, durationMs = 500;
    if (! argInt(msg, 1, note) || note < 0 || note > 127)
        return error("expected note 0..127");
    argInt(msg, 2, velocity);
    argInt(msg, 3, durationMs);
    velocity = jlimit(1, 127, velocity);
    durationMs = jlimit(10, kMaxRenderMs, durationMs);

    // MidiKeyboardState is thread safe and feeds processBlock()
    proc.keyboardState.noteOn(1, note, velocity / 127.0f);

    auto flag = alive;
    auto *p = &proc;
    Timer::callAfterDelay(durationMs, [flag, p, note] {
        if (*flag)
            p->keyboardState.noteOff(1, note, 0.0f);
    });
    return ok();
}

var DexedOscServer::cmdPanic() {
    proc.keyboardState.allNotesOff(1);
    proc.panic();
    return ok();
}

/**
 * /dexed/render <reply> <absolute .wav path> [note = 60] [velocity = 100] [hold ms = 1000] [total ms = 2000]
 *
 * Renders the current voice offline in a private engine instance, so the live
 * audio is not disturbed. Uses the live sample rate (48 kHz if not playing).
 */
var DexedOscServer::cmdRender(const OSCMessage &msg) {
    String raw;
    File f;
    if (! argString(msg, 1, raw) || ! resolvePath(raw, f))
        return error("expected an absolute path to a .wav file");
    if (! f.hasFileExtension("wav"))
        return error("the file name must end with .wav");
    if (! f.getParentDirectory().isDirectory())
        return error("directory does not exist: " + f.getParentDirectory().getFullPathName());

    int note = 60, velocity = 100, holdMs = 1000, totalMs = 2000;
    argInt(msg, 2, note);
    argInt(msg, 3, velocity);
    argInt(msg, 4, holdMs);
    argInt(msg, 5, totalMs);
    note = jlimit(0, 127, note);
    velocity = jlimit(1, 127, velocity);
    totalMs = jlimit(50, kMaxRenderMs, totalMs);
    holdMs = jlimit(0, totalMs, holdMs);

    // the msfa lookup tables are global and depend on the sample rate:
    // the offline engine must use the same rate as the live one
    double sampleRate = proc.getSampleRate();
    if (sampleRate <= 0)
        sampleRate = 48000;
    const int blockSize = 512;

    std::unique_ptr<DexedAudioProcessor> engine;
    {
        DexedAudioProcessor::constructOffline = true;
        engine.reset(new DexedAudioProcessor());
        DexedAudioProcessor::constructOffline = false;
    }

    engine->setEngineType(proc.getEngineType());
    engine->setMonoMode(proc.isMonoMode());
    engine->loadCartridge(proc.currentCart);
    memcpy(engine->data, proc.data, sizeof(proc.data));
    memcpy(engine->controllers.opSwitch, proc.controllers.opSwitch, sizeof(proc.controllers.opSwitch));
    for (int i = 0; i < proc.ctrl.size() && i < engine->ctrl.size(); i++) {
        // filter, gain, tune... (the DX7 bytes are already copied above)
        if (dynamic_cast<CtrlDX*>(proc.ctrl[i]) == nullptr)
            engine->ctrl[i]->setValueHost(proc.ctrl[i]->getValueHost());
    }
    engine->setDxValue(155, 0);   // repack op switch, flag the voice for refresh

    engine->setRateAndBufferSizeDetails(sampleRate, blockSize);
    engine->prepareToPlay(sampleRate, blockSize);

    const int totalSamples = (int) (sampleRate * totalMs / 1000.0);
    const int noteOffSample = (int) (sampleRate * holdMs / 1000.0);

    AudioBuffer<float> out(2, totalSamples);
    out.clear();
    AudioBuffer<float> block(2, blockSize);

    for (int pos = 0; pos < totalSamples; pos += blockSize) {
        const int n = jmin(blockSize, totalSamples - pos);
        block.setSize(2, n, false, false, true);
        block.clear();

        MidiBuffer midi;
        if (pos == 0)
            midi.addEvent(MidiMessage::noteOn(1, note, (uint8) velocity), 0);
        if (noteOffSample >= pos && noteOffSample < pos + n)
            midi.addEvent(MidiMessage::noteOff(1, note), noteOffSample - pos);

        engine->processBlock(block, midi);
        for (int ch = 0; ch < 2; ch++)
            out.copyFrom(ch, pos, block, ch, 0, n);
    }
    engine->releaseResources();
    engine.reset();

    f.deleteFile();
    std::unique_ptr<FileOutputStream> stream(f.createOutputStream());
    if (stream == nullptr)
        return error("unable to write " + raw);

    WavAudioFormat wav;
    std::unique_ptr<AudioFormatWriter> writer(wav.createWriterFor(stream.get(), sampleRate, 2, 24, {}, 0));
    if (writer == nullptr)
        return error("unable to create the wav writer");
    stream.release();   // now owned by the writer
    writer->writeFromAudioSampleBuffer(out, 0, totalSamples);
    writer.reset();

    DynamicObject::Ptr o = new DynamicObject();
    o->setProperty("ok", true);
    o->setProperty("path", f.getFullPathName());
    o->setProperty("sampleRate", sampleRate);
    o->setProperty("samples", totalSamples);
    o->setProperty("peak", out.getMagnitude(0, 0, totalSamples));
    o->setProperty("rms", out.getRMSLevel(0, 0, totalSamples));
    return var(o.get());
}
