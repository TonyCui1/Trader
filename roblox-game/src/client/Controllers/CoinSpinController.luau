--!nonstrict
--[[
	Spins and bobs every coin, on the client.

	This deliberately does not run on the server: the motion is cosmetic, and
	animating 45 parts server-side would replicate a CFrame update per coin
	per frame to every player. Pickup uses the coin's *server* position, which
	the local motion never touches.
]]

local RunService = game:GetService("RunService")

local CoinSpinController = {}

local SPIN_SPEED = 2.5 -- radians per second
local BOB_HEIGHT = 0.5
local BOB_SPEED = 2

function CoinSpinController.start()
	local coinsFolder = workspace:WaitForChild("Coins")
	local anchors: { [BasePart]: Vector3 } = {}
	local elapsed = 0

	coinsFolder.ChildRemoved:Connect(function(child)
		if child:IsA("BasePart") then
			anchors[child] = nil
		end
	end)

	RunService.RenderStepped:Connect(function(deltaTime)
		elapsed += deltaTime

		for _, coin in ipairs(coinsFolder:GetChildren()) do
			if not coin:IsA("BasePart") then
				continue
			end

			-- Remember where the server put the coin the first time we see it,
			-- so our bob does not drift the coin upward over its lifetime.
			local anchor = anchors[coin]
			if not anchor then
				anchor = coin.Position
				anchors[coin] = anchor
			end

			-- Offset the phase per coin so they do not bob in lockstep.
			local phase = (anchor.X + anchor.Z) * 0.35
			local height = math.sin(elapsed * BOB_SPEED + phase) * BOB_HEIGHT

			coin.CFrame = CFrame.new(anchor + Vector3.new(0, height, 0))
				* CFrame.Angles(0, elapsed * SPIN_SPEED + phase, 0)
		end
	end)
end

return CoinSpinController
