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

#ifndef DEXEDOSC_H_INCLUDED
#define DEXEDOSC_H_INCLUDED

#include "../JuceLibraryCode/JuceHeader.h"

class DexedAudioProcessor;
class CtrlDX;

/**
 * Remote control of a Dexed instance through OSC (UDP, localhost only).
 *
 * Every message's first argument is an int32 "reply port": when > 0, Dexed
 * answers on 127.0.0.1:<replyPort> with "/dexed/reply" <request address>
 * <json string>. Use 0 for fire-and-forget.
 *
 * The listening port is 9000 by default (env DEXED_OSC_PORT to change the base);
 * when busy, the next free port up to base+15 is used, so several instances
 * can live in the same DAW. Set DEXED_OSC=0 to disable the server.
 *
 * Messages are handled on the JUCE message thread, the same thread the
 * regular editor uses, so no extra locking is needed against the UI.
 *
 * See Documentation/OSC.md for the full protocol.
 */
class DexedOscServer : private OSCReceiver::Listener<OSCReceiver::MessageLoopCallback> {
public:
    explicit DexedOscServer(DexedAudioProcessor &owner);
    ~DexedOscServer() override;

    /** listening port, or -1 when the server is not running */
    int getPort() const { return port; }

private:
    void oscMessageReceived(const OSCMessage &msg) override;

    void reply(int replyPort, const String &address, const var &payload);
    void replyError(int replyPort, const String &address, const String &error);

    // handlers, return the JSON payload sent back
    var cmdPing();
    var cmdGet();
    var cmdParams();
    var cmdSet(const OSCMessage &msg);
    var cmdSetParam(const OSCMessage &msg);
    var cmdVoice(const OSCMessage &msg);
    var cmdProgram(const OSCMessage &msg);
    var cmdLoadCart(const OSCMessage &msg);
    var cmdStore(const OSCMessage &msg);
    var cmdSaveCart(const OSCMessage &msg);
    var cmdNote(const OSCMessage &msg);
    var cmdPanic();
    var cmdRender(const OSCMessage &msg);

    void markDirty();

    DexedAudioProcessor &proc;
    DatagramSocket socket;     // bound to 127.0.0.1 only
    OSCReceiver receiver;
    OSCSender sender;
    int port = -1;

    std::map<int, CtrlDX*> ctrlByOffset;   // DX7 data offset -> controller

    // keeps delayed note-off callbacks from touching a deleted processor
    std::shared_ptr<bool> alive = std::make_shared<bool>(true);

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(DexedOscServer)
};

#endif  // DEXEDOSC_H_INCLUDED
