# Miles: self-improving music agent for anything. One MCP away.

A one-page judge demo and an MCP server over the same backend.

## Run the page

```bash
~/logicagent/.venv/bin/python demo/server.py          # http://localhost:8850
```

It starts (or reuses) a Miles studio on :8851 with `MILES_STUDIO_DATA=evolve/work/ui` and `MILES_SELF_CHECK=1`, so the
producer calls `verify_songs` on its own output, then `evolve/check.py` measures the delivered audio (delivered on
time, tempo, key, clean). The prefilled trailer brief replays a real earlier run from `app/cache/` unless
**Live render** is ticked. The side panel reads the self-improvement rounds from `evolve/ledger.md`.

## Use it from any agent via MCP

Claude Code:

```bash
claude mcp add miles -- ~/logicagent/.venv/bin/python <repo>/app/mcp_server.py
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.miles]
command = "/Users/you/logicagent/.venv/bin/python"
args = ["/path/to/miles-evolve/app/mcp_server.py"]
```

Claude Desktop / any JSON config:

```json
{ "mcpServers": { "miles": { "command": "~/logicagent/.venv/bin/python",
                             "args": ["<repo>/app/mcp_server.py"] } } }
```

One tool, `make_music(brief, use_cache?)`, returns the track's path and URL plus every self-check with the measured
values (e.g. `tempo: measured 139.7 BPM, brief says 90`). It goes through the page's server on :8850 when that's up,
otherwise runs the job in-process.
