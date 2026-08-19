--!strict
--[[
	Builds the arena in code so the project runs on a completely blank place
	file -- no .rbxm assets to sync, no "it works on my machine" map drift.

	When you later build a real map in Studio, delete the call to
	`MapBuilder.build()` in the server bootstrap and instead point
	`MapBuilder.surfaces()` at the parts coins are allowed to spawn on.
]]

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local GameConfig = require(Shared.GameConfig)

local MapBuilder = {}

-- A flat top face coins may float above: centre of the face + its X/Z extents.
export type Surface = {
	center: Vector3,
	sizeX: number,
	sizeZ: number,
}

local surfaces: { Surface } = {}
local spawnPoints: { Vector3 } = {}

local PALETTE = {
	Color3.fromRGB(86, 132, 232),
	Color3.fromRGB(120, 96, 220),
	Color3.fromRGB(64, 178, 168),
	Color3.fromRGB(232, 128, 108),
}

local function makePart(name: string, size: Vector3, position: Vector3, color: Color3, parent: Instance): Part
	local part = Instance.new("Part")
	part.Name = name
	part.Size = size
	part.Position = position
	part.Anchored = true
	part.Color = color
	part.Material = Enum.Material.SmoothPlastic
	part.TopSurface = Enum.SurfaceType.Smooth
	part.BottomSurface = Enum.SurfaceType.Smooth
	part.Parent = parent
	return part
end

local function addSurface(part: Part)
	table.insert(surfaces, {
		center = part.Position + Vector3.new(0, part.Size.Y / 2, 0),
		-- Inset so coins never hang off the edge.
		sizeX = math.max(part.Size.X - 6, 2),
		sizeZ = math.max(part.Size.Z - 6, 2),
	})
end

function MapBuilder.build(): Folder
	local existing = workspace:FindFirstChild("Arena")
	if existing then
		existing:Destroy()
	end

	table.clear(surfaces)
	table.clear(spawnPoints)

	local cfg = GameConfig.Arena
	local rng = Random.new(20260819)

	local arena = Instance.new("Folder")
	arena.Name = "Arena"
	arena.Parent = workspace

	-- Floor
	local floor = makePart("Floor", cfg.FloorSize, cfg.FloorPosition, Color3.fromRGB(52, 58, 74), arena)
	floor.Material = Enum.Material.Concrete
	addSurface(floor)

	local floorTop = cfg.FloorPosition.Y + cfg.FloorSize.Y / 2

	-- Walls, so nobody falls off the edge of the world.
	local halfX = cfg.FloorSize.X / 2
	local halfZ = cfg.FloorSize.Z / 2
	local wallY = floorTop + cfg.WallHeight / 2
	local wallColor = Color3.fromRGB(38, 42, 56)

	local walls = {
		{ Vector3.new(cfg.FloorSize.X, cfg.WallHeight, cfg.WallThickness), Vector3.new(0, wallY, -halfZ) },
		{ Vector3.new(cfg.FloorSize.X, cfg.WallHeight, cfg.WallThickness), Vector3.new(0, wallY, halfZ) },
		{ Vector3.new(cfg.WallThickness, cfg.WallHeight, cfg.FloorSize.Z), Vector3.new(-halfX, wallY, 0) },
		{ Vector3.new(cfg.WallThickness, cfg.WallHeight, cfg.FloorSize.Z), Vector3.new(halfX, wallY, 0) },
	}
	for index, wall in ipairs(walls) do
		makePart("Wall" .. index, wall[1], wall[2], wallColor, arena)
	end

	-- Floating platforms: vertical play space, and more places for coins.
	for index = 1, cfg.PlatformCount do
		local size = Vector3.new(
			rng:NextNumber(cfg.PlatformMinSize.X, cfg.PlatformMaxSize.X),
			cfg.PlatformMinSize.Y,
			rng:NextNumber(cfg.PlatformMinSize.Z, cfg.PlatformMaxSize.Z)
		)
		local position = Vector3.new(
			rng:NextNumber(-halfX + 20, halfX - 20),
			floorTop + rng:NextNumber(cfg.PlatformMinHeight, cfg.PlatformMaxHeight),
			rng:NextNumber(-halfZ + 20, halfZ - 20)
		)
		local platform =
			makePart("Platform" .. index, size, position, PALETTE[(index - 1) % #PALETTE + 1], arena)
		addSurface(platform)
	end

	-- Spawn ring in the middle of the arena.
	local spawnPad = makePart(
		"SpawnPad",
		Vector3.new(40, 1, 40),
		Vector3.new(0, floorTop + 0.5, 0),
		Color3.fromRGB(240, 200, 96),
		arena
	)
	spawnPad.Material = Enum.Material.Neon

	local spawnLocation = Instance.new("SpawnLocation")
	spawnLocation.Name = "ArenaSpawn"
	spawnLocation.Size = Vector3.new(40, 1, 40)
	spawnLocation.Position = spawnPad.Position + Vector3.new(0, 1, 0)
	spawnLocation.Anchored = true
	spawnLocation.Transparency = 1
	spawnLocation.CanCollide = false
	spawnLocation.Duration = 0
	spawnLocation.Parent = arena

	for index = 1, 12 do
		local angle = math.rad((index - 1) * 30)
		table.insert(spawnPoints, Vector3.new(math.cos(angle) * 14, floorTop + 5, math.sin(angle) * 14))
	end

	return arena
end

function MapBuilder.surfaces(): { Surface }
	return surfaces
end

-- Cycles through the spawn ring so players do not stack on one another.
function MapBuilder.spawnPoint(index: number): Vector3
	if #spawnPoints == 0 then
		return Vector3.new(0, 10, 0)
	end
	return spawnPoints[(index - 1) % #spawnPoints + 1]
end

return MapBuilder
