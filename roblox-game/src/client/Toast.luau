--!nonstrict
--[[
	Short-lived messages in the middle of the screen: purchase results,
	round winners, save warnings.
]]

local TweenService = game:GetService("TweenService")

local Ui = require(script.Parent.Ui)

local Toast = {}

local container: Frame? = nil

local function ensureContainer(): Frame
	if container and container.Parent then
		return container
	end

	container = Ui.create("Frame", {
		Name = "Toasts",
		AnchorPoint = Vector2.new(0.5, 0),
		Position = UDim2.new(0.5, 0, 0, 100),
		Size = UDim2.fromOffset(420, 160),
		BackgroundTransparency = 1,
		Parent = Ui.gui(),
	}, {
		Ui.create("UIListLayout", {
			Padding = UDim.new(0, 6),
			HorizontalAlignment = Enum.HorizontalAlignment.Center,
			SortOrder = Enum.SortOrder.LayoutOrder,
		}),
	})

	return container :: Frame
end

function Toast.show(message: string, isError: boolean?)
	local parent = ensureContainer()

	local label = Ui.create("TextLabel", {
		Size = UDim2.new(1, 0, 0, 30),
		BackgroundColor3 = isError and Ui.Theme.Negative or Ui.Theme.Panel,
		BackgroundTransparency = 0.1,
		Font = Ui.Theme.Font,
		TextSize = 15,
		TextColor3 = Ui.Theme.Text,
		Text = message,
		TextTruncate = Enum.TextTruncate.AtEnd,
		Parent = parent,
	}, {
		Ui.corner(8),
		Ui.padding(4),
	})

	task.delay(2.5, function()
		local fade = TweenService:Create(label, TweenInfo.new(0.35), {
			BackgroundTransparency = 1,
			TextTransparency = 1,
		})
		fade.Completed:Connect(function()
			label:Destroy()
		end)
		fade:Play()
	end)
end

return Toast
