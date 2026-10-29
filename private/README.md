# private/

Everything personal to your collection lives here. Git ignores this folder
(except this README), and the Docker build never copies it into the image.

| File | Holds |
|---|---|
| `.env` | Paths to your photos and backup folder, API key, time zone. Start from `../.env.example`. |
| `config.toml` | Optional setting overrides. Start from `../config/config.example.toml`. |
| `denylist.txt` | Words the pre-commit hook refuses to commit: family names, places, real folder paths. One per line; `#` starts a comment. |

The running app sees this folder read-only at `/config`.

Your scans, the database and the backups are outside the repository entirely
(`PHOTO_ROOT`, the Docker volume, and `BACKUP_ROOT`).
