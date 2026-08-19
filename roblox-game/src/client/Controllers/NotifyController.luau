--!nonstrict
-- Routes server "Notify" messages to the on-screen toast stack.

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local Remotes = require(Shared.Remotes)

local Toast = require(script.Parent.Parent.Toast)

local NotifyController = {}

function NotifyController.start()
	Remotes.event("Notify").OnClientEvent:Connect(function(message: string, isError: boolean?)
		Toast.show(message, isError)
	end)
end

return NotifyController
