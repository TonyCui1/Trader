--!strict
--[[
	Player save data: coins, wins, upgrade levels.

	Rules this module follows, and that you should keep if you extend it:
	  * A load failure NEVER turns into a save. If we could not read a player's
	    data we mark the session `canSave = false` and let them play on a
	    throwaway profile rather than overwriting good data with defaults.
	  * All DataStore calls are wrapped in pcall + backoff. They fail in real
	    servers more often than you would expect.
	  * `game:BindToClose` flushes everything, so a server shutdown does not
	    eat the last few minutes of progress.
]]

local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local GameConfig = require(Shared.GameConfig)
local Remotes = require(Shared.Remotes)

-- Bump the version suffix if you ever change the shape of the data in a way
-- that old saves cannot be reconciled into.
local STORE_NAME = "CoinRush_PlayerData_v1"
local AUTOSAVE_SECONDS = 120
local MAX_ATTEMPTS = 4

export type ProfileData = {
	coins: number,
	wins: number,
	upgrades: { [string]: number },
}

type Session = {
	data: ProfileData,
	canSave: boolean,
	loaded: boolean,
}

local DataService = {}

local store = DataStoreService:GetDataStore(STORE_NAME)
local sessions: { [Player]: Session } = {}
local loading: { [Player]: boolean } = {}

local function keyFor(player: Player): string
	return "user_" .. player.UserId
end

local function defaultData(): ProfileData
	local upgrades = {}
	for _, upgrade in ipairs(GameConfig.Upgrades) do
		upgrades[upgrade.id] = 0
	end
	return {
		coins = 0,
		wins = 0,
		upgrades = upgrades,
	}
end

-- Fill in any field a save from an older version of the game is missing.
local function reconcile(raw: any): ProfileData
	local data = defaultData()
	if type(raw) ~= "table" then
		return data
	end

	if type(raw.coins) == "number" then
		data.coins = math.max(0, math.floor(raw.coins))
	end
	if type(raw.wins) == "number" then
		data.wins = math.max(0, math.floor(raw.wins))
	end
	if type(raw.upgrades) == "table" then
		for _, upgrade in ipairs(GameConfig.Upgrades) do
			local level = raw.upgrades[upgrade.id]
			if type(level) == "number" then
				data.upgrades[upgrade.id] = math.clamp(math.floor(level), 0, upgrade.maxLevel)
			end
		end
	end

	return data
end

-- Retry with exponential backoff. Returns ok, value-or-error.
local function attempt<T>(label: string, fn: () -> T): (boolean, any)
	local lastError
	for i = 1, MAX_ATTEMPTS do
		local ok, result = pcall(fn)
		if ok then
			return true, result
		end
		lastError = result
		if i < MAX_ATTEMPTS then
			task.wait(2 ^ (i - 1))
		end
	end
	warn(
		string.format(
			"[DataService] %s failed after %d attempts: %s",
			label,
			MAX_ATTEMPTS,
			tostring(lastError)
		)
	)
	return false, lastError
end

local function pushToClient(player: Player)
	local session = sessions[player]
	if not session then
		return
	end

	local leaderstats = player:FindFirstChild("leaderstats")
	if leaderstats then
		local coins = leaderstats:FindFirstChild("Coins")
		if coins and coins:IsA("IntValue") then
			coins.Value = session.data.coins
		end

		local wins = leaderstats:FindFirstChild("Wins")
		if wins and wins:IsA("IntValue") then
			wins.Value = session.data.wins
		end
	end

	Remotes.event("ProfileUpdated"):FireClient(player, session.data)
end

local function createLeaderstats(player: Player)
	local leaderstats = Instance.new("Folder")
	leaderstats.Name = "leaderstats"

	local coins = Instance.new("IntValue")
	coins.Name = "Coins"
	coins.Parent = leaderstats

	local wins = Instance.new("IntValue")
	wins.Name = "Wins"
	wins.Parent = leaderstats

	leaderstats.Parent = player
end

function DataService.load(player: Player)
	-- `start()` both connects PlayerAdded and sweeps players who are already
	-- here, so a player who joins in that window can reach this twice.
	if loading[player] then
		return
	end
	loading[player] = true

	createLeaderstats(player)

	local ok, raw = attempt("GetAsync " .. player.Name, function()
		return store:GetAsync(keyFor(player))
	end)

	-- The player may have left while we were waiting on the request.
	if not player:IsDescendantOf(Players) then
		loading[player] = nil
		return
	end

	if ok then
		sessions[player] = { data = reconcile(raw), canSave = true, loaded = true }
	else
		sessions[player] = { data = defaultData(), canSave = false, loaded = true }
		Remotes.event("Notify")
			:FireClient(player, "Could not load your save. Progress this session will not be kept.", true)
	end

	pushToClient(player)
end

function DataService.save(player: Player)
	local session = sessions[player]
	if not session or not session.canSave then
		return
	end

	local snapshot = session.data
	attempt("UpdateAsync " .. player.Name, function(): boolean
		store:UpdateAsync(keyFor(player), function()
			return snapshot
		end)
		return true
	end)
end

function DataService.release(player: Player)
	DataService.save(player)
	sessions[player] = nil
	loading[player] = nil
end

-- Yields until the player's data is available. Returns nil if they leave first.
function DataService.get(player: Player): ProfileData?
	while player:IsDescendantOf(Players) do
		local session = sessions[player]
		if session and session.loaded then
			return session.data
		end
		task.wait(0.1)
	end
	return nil
end

-- Non-yielding read, for hot paths like the coin pickup loop.
function DataService.peek(player: Player): ProfileData?
	local session = sessions[player]
	return session and session.data or nil
end

function DataService.addCoins(player: Player, amount: number): number?
	local data = DataService.peek(player)
	if not data then
		return nil
	end
	data.coins = math.max(0, data.coins + amount)
	pushToClient(player)
	return data.coins
end

function DataService.addWin(player: Player)
	local data = DataService.peek(player)
	if not data then
		return
	end
	data.wins += 1
	pushToClient(player)
end

function DataService.setUpgradeLevel(player: Player, id: string, level: number)
	local data = DataService.peek(player)
	if not data then
		return
	end
	data.upgrades[id] = level
	pushToClient(player)
end

function DataService.start()
	Players.PlayerAdded:Connect(function(player)
		task.spawn(DataService.load, player)
	end)

	-- Players who joined before this service finished starting up.
	for _, player in ipairs(Players:GetPlayers()) do
		task.spawn(DataService.load, player)
	end

	Players.PlayerRemoving:Connect(function(player)
		DataService.release(player)
	end)

	-- Periodic autosave so a crashed server loses minutes, not hours.
	task.spawn(function()
		while true do
			task.wait(AUTOSAVE_SECONDS)
			for _, player in ipairs(Players:GetPlayers()) do
				task.spawn(DataService.save, player)
			end
		end
	end)

	game:BindToClose(function()
		if RunService:IsStudio() then
			return
		end
		local pending = 0
		for _, player in ipairs(Players:GetPlayers()) do
			pending += 1
			task.spawn(function()
				DataService.save(player)
				pending -= 1
			end)
		end
		-- Roblox gives us ~30s here; wait for the saves but never forever.
		local deadline = os.clock() + 20
		while pending > 0 and os.clock() < deadline do
			task.wait(0.1)
		end
	end)
end

return DataService
