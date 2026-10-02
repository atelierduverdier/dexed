ardour {
	["type"]    = "EditorAction",
	name        = "Claude Bridge",
	license     = "GPL",
	author      = "Atelier du Verdier",
	description = [[Exécute une commande Lua déposée par le serveur MCP dexed-mcp
(~/.local/share/dexed-mcp/ardour/cmd.lua) et écrit le résultat dans result.txt.
À assigner à un emplacement d'action (Édition → Scripts Lua → Gestionnaire de scripts).]]
}

function factory () return function ()
	local dir = os.getenv ("HOME") .. "/.local/share/dexed-mcp/ardour/"

	-- encodeur JSON minimal (tables, chaînes, nombres, booléens)
	local function json (v)
		local t = type (v)
		if t == "nil" then return "null"
		elseif t == "boolean" then return v and "true" or "false"
		elseif t == "number" then
			if v ~= v or v == math.huge or v == -math.huge then return "null" end
			return string.format ("%.10g", v)
		elseif t == "string" then
			return '"' .. v:gsub ('[%c"\\]', function (c)
				local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
				return map[c] or string.format ("\\u%04x", c:byte ())
			end) .. '"'
		elseif t == "table" then
			if #v > 0 or next (v) == nil then
				local parts = {}
				for i = 1, #v do parts[#parts + 1] = json (v[i]) end
				return "[" .. table.concat (parts, ",") .. "]"
			end
			local parts = {}
			for k, val in pairs (v) do parts[#parts + 1] = json (tostring (k)) .. ":" .. json (val) end
			return "{" .. table.concat (parts, ",") .. "}"
		end
		return json (tostring (v))
	end

	local f = io.open (dir .. "cmd.lua", "r")
	if not f then
		print ("Claude Bridge : aucune commande en attente")
		return
	end
	local code = f:read ("*a")
	f:close ()
	os.remove (dir .. "cmd.lua")

	local id = code:match ("^%-%- id:(%S+)") or "?"
	local status, result
	local chunk, err = load (code, "claude", "t")
	if not chunk then
		status, result = false, "erreur de syntaxe : " .. tostring (err)
	else
		status, result = pcall (chunk, json)
		if not status then result = tostring (result) end
	end

	local out = io.open (dir .. "result.tmp", "w")
	if out then
		out:write (id .. "\n" .. (status and "ok" or "err") .. "\n")
		if type (result) == "table" then
			out:write (json (result))
		else
			out:write (json (result == nil and "" or result))
		end
		out:close ()
		os.rename (dir .. "result.tmp", dir .. "result.txt")
	end
end end

function icon (params) return function (ctx, width, height, fg)
	ctx:set_source_rgba (ARDOUR.LuaAPI.color_to_rgba (fg))
	ctx:set_line_width (1)
	ctx:move_to (width * .2, height * .3)
	ctx:line_to (width * .5, height * .5)
	ctx:line_to (width * .2, height * .7)
	ctx:stroke ()
	ctx:move_to (width * .55, height * .7)
	ctx:line_to (width * .8, height * .7)
	ctx:stroke ()
end end
