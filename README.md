# Auction Room

A real-time cricket player auction for a group of friends. One person opens a room, everyone else joins with a five-letter code, and players go under the hammer in batches: top players first, then mid, then the rest.

## How it works

- **Rooms.** The creator is the auctioneer. Rules (purse, squad size, max teams, bid timer, break between batches, order) are set in the lobby and lock when the auction starts. The bid timer takes any value from 5 to 120 seconds, and the auctioneer can also change it during the auction.
- **Batches.** With the default order, players are split into Top (85+ rating), Mid (72 to 84) and Low (under 72). Between batches there is a short interval. The auctioneer can skip the rest of a batch or skip the next one. Unsold players get one more round at the end.
- **Bidding.** The first bid on a player is at the base price; after that it is +50L or +1 Cr. Every bid resets the clock. You cannot bid more than your purse, bid when your squad is full, or outbid yourself.
- **Auctioneer controls.** Pause and resume, sell now, skip player, skip batch, change the bid timer, end auction.
- **Export.** Excel (summary, all sales, one sheet per team, unsold) or JSON, both during and after the auction.

## Under the hood

The server (`app.py`) is the single source of truth:

- All changes to a room happen under that room's lock, then the full room state is broadcast with an increasing version number. Clients ignore anything older than what they already have.
- The bid clock runs on the server. No client has to stay open for a player to be sold.
- Each bid names the lot it is for and must be exactly the next valid amount. When two people click at once, the first wins and the second is told the bid is out of date. Bids in the gap between players are rejected.
- Who you are comes from your connection, not from a name the browser sends. A secret seat token lets you rejoin after a refresh or a dropped connection. Each browser tab holds its own seat, so you can play several teams from one browser when testing.
- If the auctioneer is disconnected for 45 seconds, the gavel passes to the next connected team.
- Money is stored as whole lakhs, so 50L is shown as 50L and 1.5 Cr as 1.5 Cr.

## Player CSV

A built-in list of 527 players (`data/players.csv`) is used by default. To use your own, upload a CSV with a header row. Columns are matched by name in any order:

```csv
Name,Overall,Role,Nationality
Virat Kohli,93,Batsman,India
MS Dhoni,88,Wicketkeeper,India
```

`Name` and `Overall` (0 to 100) are required. Role spellings such as Batter, WK, Keeper or All Rounder are understood. Base prices are derived from the rating.

## Running locally

```bash
pip install -r requirements.txt
```

```bash
python app.py
```

Then open http://localhost:5000. Eventlet 0.33 does not run on Python 3.12 or newer. On those versions, start the server in threading mode instead:

```bash
SOCKETIO_ASYNC_MODE=threading python app.py
```

## Tests

```bash
pip install pytest
```

```bash
SOCKETIO_ASYNC_MODE=threading python -m pytest -q tests
```

The suite covers CSV parsing, money formatting, lobby rules, bidding and race conditions, batches and skipping, owner handover, room isolation, leave and rejoin, room cleanup, and randomized full auctions that check no player is lost or sold twice and no purse goes negative.

## Deploying on Render

`render.yaml` is ready to use: Python 3.11, `gunicorn --worker-class eventlet -w 1`, health check on `/health`. Keep it to **one worker**: rooms live in memory, so a restart or a free-tier sleep clears them.
