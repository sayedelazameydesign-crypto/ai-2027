"""Policy-gated adapter for Tencent/BrowserSkill (stdlib only).

Stage 1 implements Mode 1 (`local`: browser + daemon on the owner machine).
Modes 2 (`skill_mcp`) and 3 (`remote_wss`) are declared contracts gated on
Stage 2 and Stage 3 respectively — see `docs/BROWSERSKILL-PLAN.md` and
`docs/adr/0005-browserskill-adapter-boundary.md`.
"""
