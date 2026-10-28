# Home

A small self-hosted family organizer for Pedro and Maguy. It has a **shared shopping list**, **to-do lists**, **recipes**, a **meal planner** and a **watch next** picker.

It runs as one container with a SQLite database and sits behind Traefik + Authentik forwardAuth in the homelab (`Gorfield90/homelab-manual`) at `home-app.pmpm.dedyn.io`, built the same way as `Gorfield90/finance-manager` and `Gorfield90/training-planner`. The look is theirs (Pico, same layout and components) in warm terracotta instead of green.

## Features

- **One shared shopping list.** Everyone at home sees and edits the same list. Each item shows who added it.
- **Quick add.** Type `2 milk`, `500g flour`, `6x eggs` or `milk x2` and the amount becomes the quantity. Adding something that is already on the list updates it instead of adding a second line.
- **Grouped by aisle**, in the order you walk through the shop. A new household starts with nine aisles (Fruit & veg, Bakery, Meat & fish, …), which can be renamed, reordered, added to or deleted on the Aisles page. A product gets the aisle it had last time; the first time, a word list (English and Portuguese, `AISLE_WORDS` in `app/shopping.py`) makes a guess, else it goes under Other. The aisle can also be picked when adding.
- **Tick off in the shop.** Tapping an item puts it in the trolley (struck through, with who ticked it); tapping again puts it back. "Done shopping" takes the ticked items off the list.
- **Recently bought.** What you bought before shows as one-tap chips to put it back on the list, newest first, leaving out what is on the list already. The × drops a product from the suggestions.
- **Live.** An open list (shopping or to-do) checks every 4 seconds (only while the page is visible) whether anything changed and refreshes in place, so two phones in the shop stay in step. Taps are sent in the background, so the page doesn't reload or jump; without JavaScript every form still works as a normal post.
- **To-do lists.** Any number of lists, each shared by both of you or personal (only its creator sees it, nowhere else in the app either). A new household starts with a shared "Home" list. Tasks take an optional due date and, on shared lists, who it is for. Open tasks are sorted soonest first, undated ones last; due dates show as Today, Tomorrow, a weekday, or in red when late. Ticking a task moves it to Done (with who did it), where it can be unticked or cleared. The To-dos page shows every list with its open count and a **Coming up** list of overdue tasks and those due in the next 7 days.
- **Recipes.** A shared cookbook: name, serves, minutes, tags, ingredients (one per line, as you'd write them), steps (one per line), notes and a link to the original. Search by name, tag or ingredient, or filter by tag. On a recipe, the ingredients are ticked by default; untick what you have and "Add ticked to shopping list" puts the rest on the list with their quantities ("400 g", "1 tbsp", in English or Portuguese: "2 dentes de alho" → Alho, 2 dentes) and a note saying which recipe they're for. Something already on the list gets the quantities joined ("2 + 2") instead of a second line; "q.b." / "to taste" adds the product without a quantity.
- **Meal planner.** A week at a time (Monday to Sunday, with previous/next), lunch and dinner for each day, each from a recipe or a few words ("leftovers", "pizza night"). "Shop for this week" lists the ingredients of every recipe planned that week, each recipe once with the meals it's for; untick what you have and the rest goes on the shopping list the same way as from a recipe. Deleting a recipe keeps its name on past plans.
- **Watch next.** Each of you adds series and movies you'd like to watch, listed per person. "Pick for us" draws one at random (anything, or only a series or only a movie): first a person, with the same chance for each of you however many you added, then one of theirs. The pick shows as Up next on both phones. "We watched it" moves it to Watched, "Pick another" draws again without it, "Put it back" returns it to the list.
- **Phone first**, with a tab bar on phones, installable to the home screen (manifest + icons), light and dark mode. The browser tab always says "Home".

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `HP_USERS` | (empty) | `username:Display Name,...`. These users are created at startup. Usernames must match Authentik. |
| `HP_DATABASE_URL` | `sqlite:////data/home.db` (image) | SQLAlchemy URL. |
| `HP_AUTH_HEADER` | `X-authentik-username` | Header carrying the signed-in user. |
| `HP_DEV_USER` | (unset) | Local development only: act as this user when the header is missing. |

Migrations (Alembic, `app/migrations`) run automatically at startup.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
HP_USERS="pedro:Pedro,maguy:Maguy" HP_DEV_USER=pedro .venv/bin/uvicorn app.asgi:app --reload
```

A new migration: change `app/models.py`, then `.venv/bin/alembic revision --autogenerate -m "…" --rev-id 0002` (with `home.db` at the current head) and check the result. `tests/test_app.py` fails when the models and the migrations disagree.

The app icons are drawn by `tools/make_icons.py` (needs Pillow).

## Deployment (homelab)

Same pipeline as Finance Manager and Training Planner. CI (`.github/workflows/ci.yml`) runs the tests and pushes the private image `ghcr.io/gorfield90/home-productivity` on every push to `main` (tags `main`, `sha-…`) and on `v*` tags. The image knows its own version (`HP_VERSION`), shown under the sidebar and in `/healthz`.

### Deploying a new version

1. Merge to `main`. After the image is pushed, the `deploy-pr` job opens (or updates) one PR in `Gorfield90/homelab-manual` that bumps `newTag` in `k8s/apps/home-app/kustomization.yaml`. Without new migrations, the job merges it right away. With migrations it is titled "(DB migration)" and waits for you to merge it.
2. Once the PR is merged, Flux applies it. When the pod starts, its `predeploy-backup` initContainer writes `home-predeploy-<time>.db` to `/pool1/Backups/k8s/home-app`, then the app runs its migrations.

`deploy-pr` needs the repo secret `HOMELAB_PR_TOKEN`: a fine-grained token for `Gorfield90/homelab-manual` only, with Contents and Pull requests read/write (the one Training Planner uses works). Without it the job is skipped with a notice.

### Rollback

- **No migrations in the release:** revert the deploy PR in homelab-manual. Flux rolls the image back.
- **The release added a migration:** restore the backup this rollout took:
  1. `flux suspend kustomization apps` and `kubectl -n home-app scale deploy/home-app --replicas=0`.
  2. Copy the newest `home-predeploy-*.db` over `/data/home.db` and delete `home.db-wal` / `home.db-shm`.
  3. Revert the deploy PR, then `flux resume kustomization apps`. Anything entered after the deploy is lost.

Keep migrations additive where possible, so most releases stay revert-safe.

The homelab side (manifests, Authentik, DNS, backups) is documented in homelab-manual's `services/home-app/README.md`.

Backups: `python -m app.backup /data/home.db /backups` takes an online, integrity-checked copy. The CronJob in homelab-manual runs it nightly to `/pool1/Backups/k8s/home-app` and keeps 30 days.
