--!strict
--[[
	Spawns coins around the arena and awards them to players who get close.

	Pickup is a server-side distance check rather than a `Touched` event on
	purpose: `Touched` is driven by the collecting client's physics, which
	makes it both unreliable at speed and trivial to spoof. A radius check on
	the server is authoritative and lets the Magnet upgrade just widen a number.
]]

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local TweenService = game:GetService("TweenService")

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local GameConfig = require(Shared.GameConfig)

local DataService = require(script.Parent.DataService)
local MapBuilder = require(script.Parent.MapBuilder)

local CHECK_INTERVAL = 0.1

local CoinService = {}

-- Fired with (player, value) whenever a coin is picked up.
CoinService.Collected = Instance.new("BindableEvent")

local coinsFolder: Folder
local activeCoins: { [Part]: number } = {} -- coin -> despawn time
local coinCount = 0
local spawning = false
local rng = Random.new()

local function makeCoin(position: Vector3): Part
	local coin = Instance.new("Part")
	coin.Name = "Coin"
	coin.Shape = Enum.PartType.Cylinder
	coin.Size = Vector3.new(0.4, 3, 3)
	-- Left upright; the client spins and bobs it (see CoinSpinController).
	coin.CFrame = CFrame.new(position)
	coin.Anchored = true
	coin.CanCollide = false
	coin.CanQuery = false
	coin.CanTouch = false
	coin.Material = Enum.Material.Neon
	coin.Color = Color3.fromRGB(255, 206, 84)
	coin.Parent = coinsFolder

	local light = Instance.new("PointLight")
	light.Color = coin.Color
	light.Range = 8
	light.Brightness = 1.5
	light.Parent = coin

	return coin
end

local function despawn(coin: Part, collected: boolean)
	if not activeCoins[coin] then
		return
	end
	activeCoins[coin] = nil
	coinCount -= 1

	if not collected then
		coin:Destroy()
		return
	end

	-- Small pop so pickups read clearly. The part is already unusable
	-- because it left `activeCoins`, so the tween is purely cosmetic.
	local tween =
		TweenService:Create(coin, TweenInfo.new(0.18, Enum.EasingStyle.Back, Enum.EasingDirection.In), {
			Size = Vector3.new(0.4, 0.1, 0.1),
			Transparency = 1,
		})
	tween.Completed:Connect(function()
		coin:Destroy()
	end)
	tween:Play()
end

local function spawnOne()
	local surfaces = MapBuilder.surfaces()
	if #surfaces == 0 then
		return
	end

	local surface = surfaces[rng:NextInteger(1, #surfaces)]
	local position = surface.center
		+ Vector3.new(
			rng:NextNumber(-surface.sizeX / 2, surface.sizeX / 2),
			GameConfig.Coins.FloatHeight,
			rng:NextNumber(-surface.sizeZ / 2, surface.sizeZ / 2)
		)

	local coin = makeCoin(position)
	activeCoins[coin] = os.clock() + GameConfig.Coins.LifetimeSeconds
	coinCount += 1
end

local function collectNearbyCoins()
	local now = os.clock()

	-- Expire old coins first, so a full arena keeps cycling.
	for coin, expiry in pairs(activeCoins) do
		if now >= expiry then
			despawn(coin, false)
		end
	end

	for _, player in ipairs(Players:GetPlayers()) do
		local character = player.Character
		local root = character and character:FindFirstChild("HumanoidRootPart")
		local humanoid = character and character:FindFirstChildOfClass("Humanoid")
		if not root or not root:IsA("BasePart") or not humanoid or humanoid.Health <= 0 then
			continue
		end

		local data = DataService.peek(player)
		if not data then
			continue
		end

		local radius = GameConfig.pickupRadiusFor(data.upgrades.Magnet or 0)
		local radiusSquared = radius * radius
		local value = GameConfig.coinValueFor(data.upgrades.Fortune or 0)

		for coin in pairs(activeCoins) do
			-- Squared distance: same comparison, no square root per coin.
			if (coin.Position - root.Position).Magnitude ^ 2 <= radiusSquared then
				despawn(coin, true)
				DataService.addCoins(player, value)
				CoinService.Collected:Fire(player, value)
			end
		end
	end
end

-- Coins only spawn while a round is running.
function CoinService.setSpawning(enabled: boolean)
	spawning = enabled
end

function CoinService.clear()
	for coin in pairs(activeCoins) do
		despawn(coin, false)
	end
	coinCount = 0
end

function CoinService.start()
	coinsFolder = Instance.new("Folder")
	coinsFolder.Name = "Coins"
	coinsFolder.Parent = workspace

	task.spawn(function()
		while true do
			task.wait(GameConfig.Coins.SpawnInterval)
			if spawning and coinCount < GameConfig.Coins.MaxActive then
				spawnOne()
			end
		end
	end)

	local accumulator = 0
	RunService.Heartbeat:Connect(function(deltaTime)
		accumulator += deltaTime
		if accumulator < CHECK_INTERVAL then
			return
		end
		accumulator = 0
		collectNearbyCoins()
	end)
end

return CoinService
