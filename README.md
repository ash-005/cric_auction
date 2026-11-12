# Auction Room

A real-time cricket player auction for a group of friends. One person opens a room, everyone else joins with a five-letter code, and players go under the hammer in batches: top players first, then mid, then the rest.

## How it works

- **Rooms.** The creator is the auctioneer. Rules (purse, squad size, max teams, bid timer, break between batches, order) are set in the lobby and lock when the auction starts. The bid timer takes any value from 5 to 120 seconds, and the auctioneer can also change it during the auction.
- **Batches.** Players go under the hammer in batches, with a short interval between them. There are two ways to batch:
  - **By level:** Top (85+ rating), then Mid (72 to 84), then Low (under 72).
  - **By role:** Batsmen, then Wicket-keepers, then All-rounders, then Bowlers.

  Both can be shuffled within each batch or run best-rated first. During an interval the auctioneer can start any remaining batch next. They can also skip a batch, for example once every team has the batters it wants. Skipped players are not auctioned again. Players who got no bids get one more round at the end.
- **Bidding.** The first bid on a player is at the base price; after that it is +50L or +1 Cr. Every bid resets the clock. You cannot bid more than your purse, bid when your squad is full, or outbid yourself.
- **Auctioneer controls.** Pause and resume, sell now, skip player, skip the rest of a batch, pick which batch runs next, change the bid timer, end auction.
- **Floor view.** Every team's purse left (large), amount spent (small) and role make-up, with the role of the player up for bid highlighted. Tap a team to see its squad.
- **Starting again.** On the results screen anyone can propose a restart. If two thirds of the people still in the room vote yes within a minute, everyone goes back to the lobby with the same room code and teams, empty squads and full purses, and the auctioneer can change the rules before starting.
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

Then open http://localhost:5000. Any recent Python 3 works.

## Tests

```bash
pip install pytest
```

```bash
python -m pytest -q tests
```

The suite covers CSV parsing, money formatting, lobby rules, bidding and race conditions, batches and skipping, owner handover, room isolation, leave and rejoin, room cleanup, the bid timer, and randomized full auctions that check no player is lost or sold twice and no purse goes negative.

## Deploying on Render

`render.yaml` is ready to use: Python 3.12, `gunicorn -w 1 --threads 400 --timeout 120 --bind 0.0.0.0:$PORT app:app`, health check on `/health`. Keep it to **one worker**: rooms live in memory, so a restart or a free-tier sleep clears them.
