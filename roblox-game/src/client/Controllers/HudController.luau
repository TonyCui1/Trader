--!nonstrict
--[[
	Top-of-screen HUD: phase + countdown, your coin balance, your score this
	round, and the live round leaderboard.

	The countdown is computed locally from the server timestamp the round
	service broadcast, so it ticks smoothly without any per-second traffic.
]]

local RunService = game:GetService("RunService")

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local Remotes = require(Shared.Remotes)

local Ui = require(script.Parent.Parent.Ui)

local HudController = {}

local state = { phase = "Waiting", endsAt = 0, status = "Connecting...", leaderboard = {} }

local statusLabel: TextLabel
local timerLabel: TextLabel
local coinsLabel: TextLabel
local scoreLabel: TextLabel
local leaderboardList: Frame

local function buildTopCard(parent: Instance)
	local card = Ui.create("Frame", {
		Name = "PhaseCard",
		AnchorPoint = Vector2.new(0.5, 0),
		Position = UDim2.new(0.5, 0, 0, 12),
		Size = UDim2.fromOffset(280, 74),
		BackgroundColor3 = Ui.Theme.Panel,
		BackgroundTransparency = 0.1,
		Parent = parent,
	}, {
		Ui.corner(12),
		Ui.stroke(Ui.Theme.PanelLight),
	})

	timerLabel = Ui.create("TextLabel", {
		Name = "Timer",
		BackgroundTransparency = 1,
		Position = UDim2.new(0, 0, 0, 6),
		Size = UDim2.new(1, 0, 0, 34),
		Font = Ui.Theme.FontBold,
		TextSize = 30,
		TextColor3 = Ui.Theme.Accent,
		Text = "--:--",
		Parent = card,
	})

	statusLabel = Ui.create("TextLabel", {
		Name = "Status",
		BackgroundTransparency = 1,
		Position = UDim2.new(0, 8, 0, 42),
		Size = UDim2.new(1, -16, 0, 24),
		Font = Ui.Theme.Font,
		TextSize = 14,
		TextColor3 = Ui.Theme.TextDim,
		TextTruncate = Enum.TextTruncate.AtEnd,
		Text = "Connecting...",
		Parent = card,
	})
end

local function buildCoinCounter(parent: Instance)
	local card = Ui.create("Frame", {
		Name = "Wallet",
		AnchorPoint = Vector2.new(1, 0),
		Position = UDim2.new(1, -12, 0, 12),
		Size = UDim2.fromOffset(170, 74),
		BackgroundColor3 = Ui.Theme.Panel,
		BackgroundTransparency = 0.1,
		Parent = parent,
	}, {
		Ui.corner(12),
		Ui.stroke(Ui.Theme.PanelLight),
	})

	coinsLabel = Ui.create("TextLabel", {
		Name = "Coins",
		BackgroundTransparency = 1,
		Position = UDim2.new(0, 10, 0, 8),
		Size = UDim2.new(1, -20, 0, 32),
		Font = Ui.Theme.FontBold,
		TextSize = 24,
		TextXAlignment = Enum.TextXAlignment.Right,
		TextColor3 = Ui.Theme.Accent,
		Text = "0",
		Parent = card,
	})

	scoreLabel = Ui.create("TextLabel", {
		Name = "RoundScore",
		BackgroundTransparency = 1,
		Position = UDim2.new(0, 10, 0, 42),
		Size = UDim2.new(1, -20, 0, 22),
		Font = Ui.Theme.Font,
		TextSize = 14,
		TextXAlignment = Enum.TextXAlignment.Right,
		TextColor3 = Ui.Theme.TextDim,
		Text = "0 this round",
		Parent = card,
	})
end

local function buildLeaderboard(parent: Instance)
	local card = Ui.create("Frame", {
		Name = "RoundLeaderboard",
		AnchorPoint = Vector2.new(0, 0.5),
		Position = UDim2.new(0, 12, 0.5, 0),
		Size = UDim2.fromOffset(190, 160),
		BackgroundColor3 = Ui.Theme.Panel,
		BackgroundTransparency = 0.2,
		Parent = parent,
	}, {
		Ui.corner(12),
		Ui.stroke(Ui.Theme.PanelLight),
		Ui.padding(10),
	})

	Ui.create("TextLabel", {
		Name = "Title",
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 20),
		Font = Ui.Theme.FontBold,
		TextSize = 14,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextColor3 = Ui.Theme.Text,
		Text = "This round",
		Parent = card,
	})

	leaderboardList = Ui.create("Frame", {
		Name = "List",
		BackgroundTransparency = 1,
		Position = UDim2.new(0, 0, 0, 24),
		Size = UDim2.new(1, 0, 1, -24),
		Parent = card,
	}, {
		Ui.create("UIListLayout", {
			Padding = UDim.new(0, 4),
			SortOrder = Enum.SortOrder.LayoutOrder,
		}),
	})
end

local function renderLeaderboard()
	for _, child in ipairs(leaderboardList:GetChildren()) do
		if child:IsA("TextLabel") then
			child:Destroy()
		end
	end

	if #state.leaderboard == 0 then
		Ui.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(1, 0, 0, 18),
			Font = Ui.Theme.Font,
			TextSize = 13,
			TextXAlignment = Enum.TextXAlignment.Left,
			TextColor3 = Ui.Theme.TextDim,
			Text = "No coins yet",
			Parent = leaderboardList,
		})
		return
	end

	for index, entry in ipairs(state.leaderboard) do
		Ui.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(1, 0, 0, 18),
			LayoutOrder = index,
			Font = Ui.Theme.Font,
			TextSize = 13,
			TextXAlignment = Enum.TextXAlignment.Left,
			TextTruncate = Enum.TextTruncate.AtEnd,
			TextColor3 = index == 1 and Ui.Theme.Accent or Ui.Theme.Text,
			Text = string.format("%d. %s  -  %s", index, entry.name, Ui.formatNumber(entry.score)),
			Parent = leaderboardList,
		})
	end
end

function HudController.start()
	local gui = Ui.gui()
	buildTopCard(gui)
	buildCoinCounter(gui)
	buildLeaderboard(gui)
	renderLeaderboard()

	Remotes.event("RoundStateChanged").OnClientEvent:Connect(function(newState)
		state = newState
		statusLabel.Text = newState.status
		renderLeaderboard()
	end)

	Remotes.event("ProfileUpdated").OnClientEvent:Connect(function(profile)
		coinsLabel.Text = Ui.formatNumber(profile.coins)
	end)

	Remotes.event("ScoreUpdated").OnClientEvent:Connect(function(score)
		scoreLabel.Text = Ui.formatNumber(score) .. " this round"
	end)

	RunService.Heartbeat:Connect(function()
		local remaining = state.endsAt - workspace:GetServerTimeNow()
		timerLabel.Text = Ui.formatTime(remaining)
		timerLabel.TextColor3 = (state.phase == "Round" and remaining <= 10) and Ui.Theme.Negative
			or Ui.Theme.Accent
	end)
end

return HudController
