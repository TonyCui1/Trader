# Coin Rush — a Roblox starter game

A small but complete Roblox game you can run today and reshape into whatever
you actually want to build. It is deliberately not a framework: plain Luau,
plain services, no dependencies beyond Rojo.

**The game:** players spawn into an arena, coins keep appearing, and you have
90 seconds to grab more of them than anyone else. Coins are permanent currency
you spend on upgrades (speed, jump, magnet radius, coin value) that persist
between sessions. Highest score at the end of a round wins.

**What it demonstrates**, which is the part you'll reuse:

| Concern | Where |
| --- | --- |
| Server/client split with a single source of truth for remotes | `src/shared/Remotes.luau` |
| Tunable values in one file both sides read | `src/shared/GameConfig.luau` |
| DataStore saving that survives failures and shutdowns | `src/server/Services/DataService.luau` |
| A round loop (intermission → round → results) | `src/server/Services/RoundService.luau` |
| Server-authoritative pickups and purchases | `CoinService.luau`, `ShopService.luau` |
| A map built in code, so no asset files to sync | `src/server/Services/MapBuilder.luau` |
| UI built without a framework | `src/client/Ui.luau` + `src/client/Controllers/` |

---

## Getting started

### 1. Install the tools

You need [Roblox Studio](https://create.roblox.com/) plus a toolchain manager.
This project pins its tools with [Rokit](https://github.com/rojo-rbx/rokit):

```sh
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/rojo-rbx/rokit/main/scripts/install.sh | bash

# Windows (PowerShell)
# irm https://raw.githubusercontent.com/rojo-rbx/rokit/main/scripts/install.ps1 | iex
```

Then, from this folder:

```sh
rokit install     # installs rojo, stylua and selene at the pinned versions
```

No Rokit? `cargo install rojo` or the
[Rojo releases page](https://github.com/rojo-rbx/rojo/releases) work too.

### 2. Install the Rojo Studio plugin

```sh
rojo plugin install
```

This drops the Rojo plugin into Studio so Studio can sync from your files.

### 3. Build the place and open it

```sh
rojo build -o CoinRush.rbxlx
```

Open `CoinRush.rbxlx` in Studio (double-click it, or File → Open).

### 4. Start syncing

In one terminal:

```sh
rojo serve
```

In Studio, open the **Rojo** plugin tab → **Connect**. From now on every save
to a `.luau` file shows up in Studio immediately. Studio is your *viewer*; the
files on disk are the source of truth.

### 5. Play it

Press **Play** in Studio. You should see the arena build itself, coins start
appearing once the round begins, and the HUD in the top corners. Buy a Sprint
upgrade from the shop button and you'll feel the difference immediately.

To test with more than one player, use **Test → Clients and Servers → 2
players**.

### 6. Turn on saving (do this before you playtest progression)

DataStores are disabled in Studio by default, so upgrades won't persist until
you enable them:

**Home → Game Settings → Security → Enable Studio Access to API Services**

This requires the place to be published first (File → Publish to Roblox As…).
Until it's on, `DataService` warns in the output and lets you play on a
throwaway profile — it will never overwrite a real save with defaults.

---

## Making it yours

**Tune the game** — open `src/shared/GameConfig.luau`. Round length, coin spawn
rate, arena size, upgrade prices and effects are all there, commented. Change a
number, save, and it's live in Studio.

**Add an upgrade** — add one entry to `GameConfig.Upgrades`:

```lua
{
    id = "Doubler",
    displayName = "Lucky Streak",
    icon = "🍀",
    maxLevel = 3,
    cost = curve(200, 2.0),
    describe = function(level: number)
        return string.format("%d%% chance of a bonus coin", level * 10)
    end,
}
```

The shop UI, the purchase validation and the save format all pick it up with no
other edits. You only write code for what the upgrade actually *does*.

**Add a remote** — add its name to the list at the top of
`src/shared/Remotes.luau`. The server creates it, the client waits for it.

**Replace the arena** — delete the `MapBuilder.build()` call in
`src/server/init.server.luau`, build a map in Studio, and point
`MapBuilder.surfaces()` at the parts coins should spawn above.

**Add a feature** — new server logic goes in `src/server/Services/` with a
`.start()` function called from `init.server.luau`; new UI goes in
`src/client/Controllers/` and is listed in `init.client.luau`.

---

## Two rules worth keeping

**The server decides everything that matters.** Coin pickups are a distance
check on the server, not a `Touched` event on the client. Purchases are
validated server-side against server-held balances. The client UI is a *view* —
a hacked client can ask for anything and get told no. If you add a feature that
grants currency or power, add it on the server.

**Never let a failed load become a save.** If `DataService` can't read a
player's data it marks the session unsaveable rather than handing them a fresh
profile that will overwrite their real one. DataStore calls fail more often in
live servers than in Studio; plan for it.

---

## Commands

```sh
rojo build -o CoinRush.rbxlx   # build a place file
rojo serve                     # live-sync into Studio
rojo sourcemap -o sourcemap.json   # for luau-lsp autocomplete

stylua src                     # format
selene src                     # lint (run `selene generate-roblox-std` once first)
```

Editor setup: install the **Luau Language Server** and **StyLua** extensions in
VS Code. `.vscode/settings.json` here already points them at the Rojo project,
so `workspace`, `Players`, and your own modules autocomplete correctly.

---

## Where to go next

Ideas that fit the existing structure, roughly easiest first:

- A sound on pickup (`SoundService`, played from `CoinSpinController`).
- Rare coins worth 10× with a different colour — one branch in `CoinService.spawnOne`.
- A global all-time leaderboard using `OrderedDataStore` next to `DataService`.
- Power-ups on the ground: double coins for 15 seconds.
- Gamepasses / developer products (`MarketplaceService`) for a permanent boost.
- Multiple maps, chosen at random per round in `RoundService`.
