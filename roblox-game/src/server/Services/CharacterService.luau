--!strict
--[[
	Applies the player's upgrade levels to their Humanoid, on every spawn and
	again whenever they buy something.
]]

local Players = game:GetService("Players")

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local GameConfig = require(Shared.GameConfig)

local DataService = require(script.Parent.DataService)

local CharacterService = {}

function CharacterService.applyUpgrades(player: Player)
	local character = player.Character
	local humanoid = character and character:FindFirstChildOfClass("Humanoid")
	if not humanoid then
		return
	end

	local data = DataService.peek(player)
	if not data then
		return
	end

	humanoid.WalkSpeed = GameConfig.walkSpeedFor(data.upgrades.Speed or 0)
	humanoid.UseJumpPower = true
	humanoid.JumpPower = GameConfig.jumpPowerFor(data.upgrades.Jump or 0)
end

function CharacterService.start()
	local function hookPlayer(player: Player)
		player.CharacterAdded:Connect(function()
			-- Data may still be loading on the player's first spawn.
			task.spawn(function()
				DataService.get(player)
				CharacterService.applyUpgrades(player)
			end)
		end)

		if player.Character then
			CharacterService.applyUpgrades(player)
		end
	end

	Players.PlayerAdded:Connect(hookPlayer)
	for _, player in ipairs(Players:GetPlayers()) do
		hookPlayer(player)
	end
end

return CharacterService
