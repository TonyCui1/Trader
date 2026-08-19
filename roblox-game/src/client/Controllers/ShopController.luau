--!nonstrict
--[[
	Upgrade shop. Purely a view: it renders what the server says the player
	owns and asks the server to buy things. It never changes a balance itself.
]]

local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local GameConfig = require(Shared.GameConfig)
local Remotes = require(Shared.Remotes)

local Toast = require(script.Parent.Parent.Toast)
local Ui = require(script.Parent.Parent.Ui)

local ShopController = {}

local profile = { coins = 0, wins = 0, upgrades = {} }
local rows: { [string]: { button: TextButton, level: TextLabel, detail: TextLabel } } = {}
local panel: Frame
local busy = false

local function refresh()
	for _, upgrade in ipairs(GameConfig.Upgrades) do
		local row = rows[upgrade.id]
		if not row then
			continue
		end

		local level = profile.upgrades[upgrade.id] or 0
		row.level.Text = string.format("Level %d / %d", level, upgrade.maxLevel)
		row.detail.Text = upgrade.describe(level)

		if level >= upgrade.maxLevel then
			row.button.Text = "MAX"
			row.button.BackgroundColor3 = Ui.Theme.PanelLight
			row.button.Active = false
		else
			local cost = upgrade.cost(level)
			row.button.Text = Ui.formatNumber(cost)
			row.button.BackgroundColor3 = profile.coins >= cost and Ui.Theme.Positive or Ui.Theme.PanelLight
			row.button.Active = true
		end
	end
end

local function buy(upgradeId: string)
	if busy then
		return
	end
	busy = true

	local ok, result = pcall(function()
		return Remotes.fn("PurchaseUpgrade"):InvokeServer(upgradeId)
	end)

	busy = false

	if ok and type(result) == "table" then
		Toast.show(result.message, not result.ok)
	else
		Toast.show("Could not reach the shop. Try again.", true)
	end
end

local function buildRow(upgrade, index: number, parent: Instance)
	local row = Ui.create("Frame", {
		Name = upgrade.id,
		Size = UDim2.new(1, 0, 0, 76),
		LayoutOrder = index,
		BackgroundColor3 = Ui.Theme.PanelLight,
		BackgroundTransparency = 0.35,
		Parent = parent,
	}, {
		Ui.corner(10),
		Ui.padding(10),
	})

	Ui.create("TextLabel", {
		Name = "Icon",
		BackgroundTransparency = 1,
		Size = UDim2.fromOffset(34, 34),
		Font = Ui.Theme.FontBold,
		TextSize = 24,
		TextColor3 = Ui.Theme.Accent,
		Text = upgrade.icon,
		Parent = row,
	})

	Ui.create("TextLabel", {
		Name = "Title",
		BackgroundTransparency = 1,
		Position = UDim2.new(0, 42, 0, 0),
		Size = UDim2.new(1, -142, 0, 20),
		Font = Ui.Theme.FontBold,
		TextSize = 15,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextColor3 = Ui.Theme.Text,
		Text = upgrade.displayName,
		Parent = row,
	})

	local detail = Ui.create("TextLabel", {
		Name = "Detail",
		BackgroundTransparency = 1,
		Position = UDim2.new(0, 42, 0, 20),
		Size = UDim2.new(1, -142, 0, 18),
		Font = Ui.Theme.Font,
		TextSize = 13,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextTruncate = Enum.TextTruncate.AtEnd,
		TextColor3 = Ui.Theme.TextDim,
		Text = "",
		Parent = row,
	})

	local level = Ui.create("TextLabel", {
		Name = "Level",
		BackgroundTransparency = 1,
		Position = UDim2.new(0, 42, 0, 38),
		Size = UDim2.new(1, -142, 0, 18),
		Font = Ui.Theme.Font,
		TextSize = 12,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextColor3 = Ui.Theme.TextDim,
		Text = "",
		Parent = row,
	})

	local button = Ui.create("TextButton", {
		Name = "Buy",
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, 0, 0.5, 0),
		Size = UDim2.fromOffset(92, 36),
		BackgroundColor3 = Ui.Theme.PanelLight,
		AutoButtonColor = true,
		Font = Ui.Theme.FontBold,
		TextSize = 15,
		TextColor3 = Ui.Theme.Background,
		Text = "-",
		Parent = row,
	}, {
		Ui.corner(8),
	})

	button.Activated:Connect(function()
		buy(upgrade.id)
	end)

	rows[upgrade.id] = { button = button, level = level, detail = detail }
end

local function buildPanel(parent: Instance)
	panel = Ui.create("Frame", {
		Name = "Shop",
		AnchorPoint = Vector2.new(0.5, 0.5),
		Position = UDim2.new(0.5, 0, 0.5, 0),
		Size = UDim2.fromOffset(430, 400),
		BackgroundColor3 = Ui.Theme.Background,
		Visible = false,
		Parent = parent,
	}, {
		Ui.corner(16),
		Ui.stroke(Ui.Theme.PanelLight),
		Ui.padding(16),
	})

	Ui.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 28),
		Font = Ui.Theme.FontBold,
		TextSize = 20,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextColor3 = Ui.Theme.Text,
		Text = "Upgrades",
		Parent = panel,
	})

	local close = Ui.create("TextButton", {
		AnchorPoint = Vector2.new(1, 0),
		Position = UDim2.new(1, 0, 0, 0),
		Size = UDim2.fromOffset(28, 28),
		BackgroundColor3 = Ui.Theme.PanelLight,
		Font = Ui.Theme.FontBold,
		TextSize = 16,
		TextColor3 = Ui.Theme.Text,
		Text = "X",
		Parent = panel,
	}, {
		Ui.corner(8),
	})
	close.Activated:Connect(function()
		panel.Visible = false
	end)

	local list = Ui.create("ScrollingFrame", {
		Position = UDim2.new(0, 0, 0, 38),
		Size = UDim2.new(1, 0, 1, -38),
		BackgroundTransparency = 1,
		BorderSizePixel = 0,
		ScrollBarThickness = 4,
		CanvasSize = UDim2.new(),
		AutomaticCanvasSize = Enum.AutomaticSize.Y,
		Parent = panel,
	}, {
		Ui.create("UIListLayout", {
			Padding = UDim.new(0, 8),
			SortOrder = Enum.SortOrder.LayoutOrder,
		}),
	})

	for index, upgrade in ipairs(GameConfig.Upgrades) do
		buildRow(upgrade, index, list)
	end
end

local function buildToggle(parent: Instance)
	local button = Ui.create("TextButton", {
		Name = "ShopToggle",
		AnchorPoint = Vector2.new(1, 1),
		Position = UDim2.new(1, -12, 1, -12),
		Size = UDim2.fromOffset(150, 46),
		BackgroundColor3 = Ui.Theme.Accent,
		Font = Ui.Theme.FontBold,
		TextSize = 16,
		TextColor3 = Ui.Theme.Background,
		Text = "SHOP",
		Parent = parent,
	}, {
		Ui.corner(12),
	})

	button.Activated:Connect(function()
		panel.Visible = not panel.Visible
	end)
end

function ShopController.start()
	local gui = Ui.gui()
	buildPanel(gui)
	buildToggle(gui)
	refresh()

	Remotes.event("ProfileUpdated").OnClientEvent:Connect(function(newProfile)
		profile = newProfile
		refresh()
	end)
end

return ShopController
