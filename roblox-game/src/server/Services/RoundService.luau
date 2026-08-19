--!strict
--[[
	The game loop: Intermission -> Round -> Results, forever.

	The server broadcasts *when the phase ends* (a server timestamp), not a
	ticking number. Clients count down locally against
	`workspace:GetServerTimeNow()`, so the timer stays smooth without a
	remote call every second.
]]

local Players = game:GetService("Players")

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local GameConfig = require(Shared.GameConfig)
local Remotes = require(Shared.Remotes)

local CoinService = require(script.Parent.CoinService)
local DataService = require(script.Parent.DataService)
local MapBuilder = require(script.Parent.MapBuilder)

local RoundService = {}

export type Phase = "Waiting" | "Intermission" | "Round" | "Results"

local phase: Phase = "Waiting"
local phaseEndsAt = 0
local statusText = ""
local roundScores: { [Player]: number } = {}

local function broadcast()
	local leaderboard = {}
	for player, score in pairs(roundScores) do
		if score > 0 then
			table.insert(leaderboard, { name = player.DisplayName, score = score })
		end
	end
	table.sort(leaderboard, function(a, b)
		return a.score > b.score
	end)
	-- Only the top few matter on screen.
	while #leaderboard > 5 do
		table.remove(leaderboard)
	end

	Remotes.event("RoundStateChanged"):FireAllClients({
		phase = phase,
		endsAt = phaseEndsAt,
		status = statusText,
		leaderboard = leaderboard,
	})
end

local function setPhase(newPhase: Phase, duration: number, status: string)
	phase = newPhase
	phaseEndsAt = workspace:GetServerTimeNow() + duration
	statusText = status
	broadcast()
end

local function teleportPlayersToArena()
	local index = 0
	for _, player in ipairs(Players:GetPlayers()) do
		index += 1
		local character = player.Character
		local root = character and character:FindFirstChild("HumanoidRootPart")
		if root and root:IsA("BasePart") then
			root.CFrame = CFrame.new(MapBuilder.spawnPoint(index))
		end
	end
end

local function resetScores()
	table.clear(roundScores)
	for _, player in ipairs(Players:GetPlayers()) do
		roundScores[player] = 0
		Remotes.event("ScoreUpdated"):FireClient(player, 0)
	end
end

local function winner(): (Player?, number)
	local best: Player? = nil
	local bestScore = 0
	for player, score in pairs(roundScores) do
		if score > bestScore and player:IsDescendantOf(Players) then
			best = player
			bestScore = score
		end
	end
	return best, bestScore
end

-- Waits out the current phase, but returns early if `shouldAbort` says so.
local function waitForPhase(shouldAbort: (() -> boolean)?)
	while workspace:GetServerTimeNow() < phaseEndsAt do
		if shouldAbort and shouldAbort() then
			return
		end
		task.wait(0.25)
	end
end

local function enoughPlayers(): boolean
	return #Players:GetPlayers() >= GameConfig.Round.MinPlayers
end

function RoundService.currentState()
	return { phase = phase, endsAt = phaseEndsAt, status = statusText, leaderboard = {} }
end

function RoundService.start()
	CoinService.Collected.Event:Connect(function(player: Player, value: number)
		if phase ~= "Round" then
			return
		end
		local score = (roundScores[player] or 0) + value
		roundScores[player] = score
		Remotes.event("ScoreUpdated"):FireClient(player, score)
	end)

	Players.PlayerAdded:Connect(function(player)
		roundScores[player] = roundScores[player] or 0
		-- Give the client a moment to set up its listeners before the first state push.
		task.delay(1, broadcast)
	end)

	Players.PlayerRemoving:Connect(function(player)
		roundScores[player] = nil
	end)

	task.spawn(function()
		while true do
			if not enoughPlayers() then
				setPhase(
					"Waiting",
					math.huge,
					string.format("Waiting for players (%d needed)", GameConfig.Round.MinPlayers)
				)
				repeat
					task.wait(1)
				until enoughPlayers()
			end

			setPhase("Intermission", GameConfig.Round.IntermissionSeconds, "Next round starting soon")
			waitForPhase(function()
				return not enoughPlayers()
			end)

			if not enoughPlayers() then
				continue
			end

			resetScores()
			teleportPlayersToArena()
			setPhase("Round", GameConfig.Round.RoundSeconds, "Grab every coin you can!")
			CoinService.setSpawning(true)

			-- Refresh the live leaderboard a few times a second.
			local roundActive = true
			task.spawn(function()
				while roundActive do
					task.wait(1)
					if roundActive then
						broadcast()
					end
				end
			end)

			waitForPhase(function()
				return not enoughPlayers()
			end)
			roundActive = false

			CoinService.setSpawning(false)
			CoinService.clear()

			local best, bestScore = winner()
			if best then
				DataService.addWin(best)
				setPhase("Results", 6, string.format("%s wins with %d coins!", best.DisplayName, bestScore))
				Remotes.event("Notify"):FireAllClients(
					string.format("%s wins the round with %d coins!", best.DisplayName, bestScore),
					false
				)
			else
				setPhase("Results", 4, "Nobody collected a single coin.")
			end

			waitForPhase()
		end
	end)
end

return RoundService
