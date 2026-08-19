--!strict
--[[
	Client entry point. Rojo maps this folder to
	StarterPlayerScripts.Client, so `script.Controllers.X` and `script.Ui`
	resolve to the sibling files.
]]

local Controllers = script.Controllers

local HudController = require(Controllers.HudController)
local ShopController = require(Controllers.ShopController)
local CoinSpinController = require(Controllers.CoinSpinController)
local NotifyController = require(Controllers.NotifyController)

-- Each controller owns one slice of the screen and never reaches into another.
-- Adding a feature usually means adding a file here, not editing an old one.
for _, controller in ipairs({
	HudController,
	ShopController,
	NotifyController,
	CoinSpinController,
}) do
	local ok, err = pcall(controller.start)
	if not ok then
		warn("[CoinRush] controller failed to start: " .. tostring(err))
	end
end
