--!strict
--[[
	Single source of truth for every RemoteEvent / RemoteFunction.

	The server creates the instances the first time this module is required;
	the client waits for them. Adding a remote means adding one string below --
	nothing else in the codebase needs to change.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local EVENTS = {
	-- Server -> client: the player's saved profile changed (coins, wins, upgrades).
	"ProfileUpdated",
	-- Server -> client: round phase / timer changed.
	"RoundStateChanged",
	-- Server -> client: this player's score inside the current round changed.
	"ScoreUpdated",
	-- Server -> client: short toast message.
	"Notify",
}

local FUNCTIONS = {
	-- Client -> server: attempt to buy one level of an upgrade.
	"PurchaseUpgrade",
}

local Remotes = {}

local folder: Folder

if RunService:IsServer() then
	local existing = ReplicatedStorage:FindFirstChild("Remotes")
	if existing and existing:IsA("Folder") then
		folder = existing
	else
		folder = Instance.new("Folder")
		folder.Name = "Remotes"
		folder.Parent = ReplicatedStorage
	end

	for _, name in ipairs(EVENTS) do
		if not folder:FindFirstChild(name) then
			local remote = Instance.new("RemoteEvent")
			remote.Name = name
			remote.Parent = folder
		end
	end

	for _, name in ipairs(FUNCTIONS) do
		if not folder:FindFirstChild(name) then
			local remote = Instance.new("RemoteFunction")
			remote.Name = name
			remote.Parent = folder
		end
	end
else
	folder = ReplicatedStorage:WaitForChild("Remotes") :: Folder
end

function Remotes.event(name: string): RemoteEvent
	return folder:WaitForChild(name) :: RemoteEvent
end

function Remotes.fn(name: string): RemoteFunction
	return folder:WaitForChild(name) :: RemoteFunction
end

return Remotes
