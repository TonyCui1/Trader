--!nonstrict
--[[
	Tiny helper layer over Instance.new so the UI code below reads as a tree
	instead of 200 lines of property assignments. There is no UI framework
	here on purpose -- one less thing to learn while you are getting started.
]]

local Players = game:GetService("Players")

local Ui = {}

Ui.Theme = {
	Background = Color3.fromRGB(24, 26, 34),
	Panel = Color3.fromRGB(32, 35, 46),
	PanelLight = Color3.fromRGB(44, 48, 62),
	Accent = Color3.fromRGB(255, 206, 84),
	Positive = Color3.fromRGB(96, 208, 140),
	Negative = Color3.fromRGB(236, 110, 96),
	Text = Color3.fromRGB(236, 239, 246),
	TextDim = Color3.fromRGB(158, 165, 182),
	Font = Enum.Font.GothamMedium,
	FontBold = Enum.Font.GothamBold,
}

--[[
	Ui.create("TextLabel", { Text = "hi" }, { child1, child2 })
]]
function Ui.create(className: string, props: { [string]: any }?, children: { Instance }?): any
	local instance = Instance.new(className)

	for key, value in pairs(props or {}) do
		if key ~= "Parent" then
			instance[key] = value
		end
	end

	for _, child in ipairs(children or {}) do
		child.Parent = instance
	end

	-- Parent last: fewer replication updates, and children exist before the
	-- instance enters the DataModel.
	if props and props.Parent then
		instance.Parent = props.Parent
	end

	return instance
end

function Ui.corner(radius: number): UICorner
	return Ui.create("UICorner", { CornerRadius = UDim.new(0, radius) })
end

function Ui.padding(pixels: number): UIPadding
	return Ui.create("UIPadding", {
		PaddingTop = UDim.new(0, pixels),
		PaddingBottom = UDim.new(0, pixels),
		PaddingLeft = UDim.new(0, pixels),
		PaddingRight = UDim.new(0, pixels),
	})
end

function Ui.stroke(color: Color3, thickness: number?): UIStroke
	return Ui.create("UIStroke", {
		Color = color,
		Thickness = thickness or 1,
		ApplyStrokeMode = Enum.ApplyStrokeMode.Border,
	})
end

-- 12345 -> "12,345"
function Ui.formatNumber(value: number): string
	local text = tostring(math.floor(value))
	local formatted = text:reverse():gsub("(%d%d%d)", "%1,"):reverse()
	return (formatted:gsub("^,", ""))
end

-- 95 -> "1:35"
function Ui.formatTime(seconds: number): string
	if seconds == math.huge then
		return "--:--"
	end
	seconds = math.max(0, math.floor(seconds))
	return string.format("%d:%02d", seconds // 60, seconds % 60)
end

local screenGui: ScreenGui? = nil

-- One ScreenGui shared by every controller.
function Ui.gui(): ScreenGui
	if screenGui and screenGui.Parent then
		return screenGui
	end

	local playerGui = Players.LocalPlayer:WaitForChild("PlayerGui")
	screenGui = Ui.create("ScreenGui", {
		Name = "CoinRush",
		ResetOnSpawn = false,
		IgnoreGuiInset = false,
		ZIndexBehavior = Enum.ZIndexBehavior.Sibling,
		Parent = playerGui,
	})

	return screenGui :: ScreenGui
end

return Ui
