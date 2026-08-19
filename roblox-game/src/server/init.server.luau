--!strict
--[[
	Server entry point. Rojo maps this whole folder to
	ServerScriptService.Server, so `script.Services.X` resolves to the
	sibling module files.

	Order matters: Remotes must exist before any service tries to use one,
	and DataService must be listening for PlayerAdded before the first player
	can join.
]]

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")

-- Requiring this on the server creates the RemoteEvent / RemoteFunction
-- instances the clients are already waiting on.
require(Shared.Remotes)

local Services = script.Services

local DataService = require(Services.DataService)
local MapBuilder = require(Services.MapBuilder)
local CharacterService = require(Services.CharacterService)
local CoinService = require(Services.CoinService)
local RoundService = require(Services.RoundService)
local ShopService = require(Services.ShopService)

MapBuilder.build()

DataService.start()
CharacterService.start()
CoinService.start()
ShopService.start()
RoundService.start()

print("[CoinRush] server ready")
