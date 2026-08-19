--!strict
--[[
	Upgrade purchases. Every check happens here on the server -- the client's
	shop UI is only a view, and a hacked client asking to buy level 99 for
	free is rejected by the same code path as a legitimate request.
]]

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local GameConfig = require(Shared.GameConfig)
local Remotes = require(Shared.Remotes)

local CharacterService = require(script.Parent.CharacterService)
local DataService = require(script.Parent.DataService)

local ShopService = {}

export type PurchaseResult = {
	ok: boolean,
	message: string,
}

local function purchase(player: Player, upgradeId: unknown): PurchaseResult
	if type(upgradeId) ~= "string" then
		return { ok = false, message = "Unknown upgrade." }
	end

	local upgrade = GameConfig.getUpgrade(upgradeId)
	if not upgrade then
		return { ok = false, message = "Unknown upgrade." }
	end

	local data = DataService.peek(player)
	if not data then
		return { ok = false, message = "Your data is still loading." }
	end

	local level = data.upgrades[upgrade.id] or 0
	if level >= upgrade.maxLevel then
		return { ok = false, message = upgrade.displayName .. " is already maxed." }
	end

	local cost = upgrade.cost(level)
	if data.coins < cost then
		return { ok = false, message = string.format("You need %d more coins.", cost - data.coins) }
	end

	DataService.addCoins(player, -cost)
	DataService.setUpgradeLevel(player, upgrade.id, level + 1)
	CharacterService.applyUpgrades(player)

	return {
		ok = true,
		message = string.format("%s is now level %d.", upgrade.displayName, level + 1),
	}
end

function ShopService.start()
	Remotes.fn("PurchaseUpgrade").OnServerInvoke = function(player: Player, upgradeId: unknown)
		local ok, result = pcall(purchase, player, upgradeId)
		if not ok then
			warn("[ShopService] purchase failed: " .. tostring(result))
			return { ok = false, message = "Something went wrong." }
		end
		return result
	end
end

return ShopService
