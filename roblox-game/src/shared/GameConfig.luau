--!strict
--[[
	Every number you would want to tune while playtesting lives here.
	Both the server and the client read this module, so a change is applied
	in one place and stays consistent across the whole game.
]]

local GameConfig = {}

GameConfig.Round = {
	-- Seconds spent waiting between rounds.
	IntermissionSeconds = 12,
	-- Seconds a round lasts.
	RoundSeconds = 90,
	-- Rounds will not start below this player count (set to 1 for solo testing).
	MinPlayers = 1,
}

GameConfig.Coins = {
	-- How many coins may exist in the arena at once.
	MaxActive = 45,
	-- Seconds between spawn attempts.
	SpawnInterval = 0.4,
	-- Currency granted per coin, before upgrade multipliers.
	BaseValue = 1,
	-- A coin despawns after this long if nobody grabs it.
	LifetimeSeconds = 30,
	-- Studs above the floor the coin floats at.
	FloatHeight = 3.5,
	-- Base pickup radius in studs; the Magnet upgrade adds to this.
	BasePickupRadius = 5,
}

GameConfig.Arena = {
	FloorSize = Vector3.new(240, 4, 240),
	FloorPosition = Vector3.new(0, 0, 0),
	WallHeight = 24,
	WallThickness = 4,
	-- Decorative platforms the coins can also spawn on top of.
	PlatformCount = 18,
	PlatformMinSize = Vector3.new(14, 2, 14),
	PlatformMaxSize = Vector3.new(30, 2, 30),
	PlatformMinHeight = 8,
	PlatformMaxHeight = 34,
}

GameConfig.Character = {
	BaseWalkSpeed = 20,
	BaseJumpPower = 55,
}

--[[
	Upgrades are purchased with coins and persist across sessions.
	`cost(level)` is the price to go from `level` to `level + 1`.
	`describe(level)` is the text shown in the shop for the *current* level.
]]
export type Upgrade = {
	id: string,
	displayName: string,
	icon: string,
	maxLevel: number,
	-- Price to go from `level` to `level + 1`.
	cost: (level: number) -> number,
	-- Text shown in the shop for the *current* level.
	describe: (level: number) -> string,
}

-- Geometric price curve: each level costs `growth` times the previous one.
local function curve(base: number, growth: number): (number) -> number
	return function(level: number): number
		return math.floor(base * growth ^ level + 0.5)
	end
end

GameConfig.Upgrades = {
	{
		id = "Speed",
		displayName = "Sprint",
		icon = "\u{26A1}",
		maxLevel = 6,
		cost = curve(40, 1.75),
		describe = function(level: number)
			return string.format("Walk speed %d", GameConfig.Character.BaseWalkSpeed + level * 3)
		end,
	},
	{
		id = "Jump",
		displayName = "Spring Legs",
		icon = "\u{1F998}",
		maxLevel = 6,
		cost = curve(55, 1.8),
		describe = function(level: number)
			return string.format("Jump power %d", GameConfig.Character.BaseJumpPower + level * 6)
		end,
	},
	{
		id = "Magnet",
		displayName = "Coin Magnet",
		icon = "\u{1F9F2}",
		maxLevel = 5,
		cost = curve(90, 2.05),
		describe = function(level: number)
			return string.format("Pickup radius %d studs", GameConfig.Coins.BasePickupRadius + level * 4)
		end,
	},
	{
		id = "Fortune",
		displayName = "Fortune",
		icon = "\u{1F48E}",
		maxLevel = 5,
		cost = curve(150, 2.3),
		describe = function(level: number)
			return string.format("Coins are worth %d%%", 100 + level * 50)
		end,
	},
} :: { Upgrade }

function GameConfig.getUpgrade(id: string): Upgrade?
	for _, upgrade in ipairs(GameConfig.Upgrades) do
		if upgrade.id == id then
			return upgrade
		end
	end
	return nil
end

-- Derived stats, kept here so server and client never disagree.
function GameConfig.walkSpeedFor(level: number): number
	return GameConfig.Character.BaseWalkSpeed + level * 3
end

function GameConfig.jumpPowerFor(level: number): number
	return GameConfig.Character.BaseJumpPower + level * 6
end

function GameConfig.pickupRadiusFor(level: number): number
	return GameConfig.Coins.BasePickupRadius + level * 4
end

function GameConfig.coinValueFor(level: number): number
	return math.floor(GameConfig.Coins.BaseValue * (1 + level * 0.5) + 0.5)
end

return GameConfig
