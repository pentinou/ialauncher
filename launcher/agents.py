"""Outils de code (Claude Code, Codex, OpenCode) branchés sur llama-server.

llama-server parle nativement les trois dialectes attendus :
  • /v1/messages (+ count_tokens)  — API Anthropic, utilisée par Claude Code
  • /v1/responses                  — API Responses d'OpenAI, utilisée par Codex
  • /v1/chat/completions           — API Chat, utilisée par OpenCode (et presque tout le reste)
Il suffit donc de dire à chaque outil où est le serveur : variables d'environnement
(Claude Code), options -c en ligne de commande (Codex), configuration inline
(OpenCode). Aucun fichier de configuration de l'utilisateur n'est modifié : chaque
outil reçoit ses réglages au lancement, via un script généré."""
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import urllib.request

from . import paths


def _which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def installed():
    return {"claude": _which("claude"), "codex": _which("codex"), "opencode": _which("opencode")}


def _env_export(env, shell):
    if shell == "cmd":
        return "\n".join(f"set {k}={v}" for k, v in env.items())
    if shell == "powershell":
        return "\n".join(f"$env:{k} = '{v}'" for k, v in env.items())
    return "\n".join(f"export {k}={shlex.quote(v)}" for k, v in env.items())


def normalize_anthropic(body):
    """Requête Anthropic « propre » : tout message de rôle system trouvé dans la liste
    des messages est déplacé dans le champ system de tête (les gabarits Qwen, entre
    autres, refusent un system ailleurs qu'en première position)."""
    try:
        d = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return body
    msgs = d.get("messages")
    if not isinstance(msgs, list) or not any(isinstance(m, dict) and m.get("role") == "system" for m in msgs):
        return body
    sys_blocks = d.get("system") or []
    if isinstance(sys_blocks, str):
        sys_blocks = [{"type": "text", "text": sys_blocks}]
    kept = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "system":
            c = m.get("content")
            if isinstance(c, str):
                sys_blocks.append({"type": "text", "text": c})
            elif isinstance(c, list):
                sys_blocks.extend(b for b in c if isinstance(b, dict) and b.get("type") == "text")
        else:
            kept.append(m)
    d["system"] = sys_blocks
    d["messages"] = kept
    return json.dumps(d, ensure_ascii=False).encode("utf-8")


def specs(base_url, alias, ctx, proxy_url=None, vision=False):
    """Pour chaque outil : env, commande, explication. base_url sans /v1 ; proxy_url =
    relais du launcher (normalise les requêtes de Claude Code) ; vision = le serveur a
    chargé un mmproj (OpenCode refuse les images tant qu'on ne le lui déclare pas)."""
    claude_env = {
        "ANTHROPIC_BASE_URL": proxy_url or base_url,
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "local",
        "ANTHROPIC_MODEL": alias,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": alias,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": alias,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": alias,
        "ANTHROPIC_SMALL_FAST_MODEL": alias,
        "CLAUDE_CODE_SUBAGENT_MODEL": alias,
        # Claude Code compacte la conversation avant d'atteindre cette taille : on lui
        # donne la taille RÉELLE de notre contexte, sinon il vise 200k et déborde.
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(int(ctx)),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_ERROR_REPORTING": "1",
    }
    codex_overrides = [
        'model_provider="llamacpp"',
        'model_providers.llamacpp.name="llama.cpp (local)"',
        f'model_providers.llamacpp.base_url="{base_url}/v1"',
        'model_providers.llamacpp.wire_api="responses"',
    ]
    codex_cmd = "codex " + " ".join("-c " + shlex.quote(o) for o in codex_overrides) + f" -m {shlex.quote(alias)}"
    opencode_cfg = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"llamacpp": {"npm": "@ai-sdk/openai-compatible", "name": "llama.cpp (local)",
                                  "options": {"baseURL": base_url + "/v1"},
                                  "models": {alias: {"name": alias, "limit": {"context": int(ctx), "output": 32768},
                                                     "modalities": {"input": ["text", "image"] if vision else ["text"],
                                                                    "output": ["text"]}}}}},
        "model": f"llamacpp/{alias}",
    }
    return {
        "claude": {
            "name": "Claude Code", "env": claude_env, "cmd": "claude",
            "how": "Variables d'environnement : Claude Code parle l'API Anthropic, que llama-server expose sur "
                   "/v1/messages. Les requêtes passent par le relais du launcher, qui remet en tête le message "
                   "« system » que Claude Code glisse au milieu de la conversation (les gabarits Qwen le refusent). "
                   "Tous les niveaux de modèle (opus/sonnet/haiku, sous-agents) pointent sur le modèle local ; "
                   "CLAUDE_CODE_AUTO_COMPACT_WINDOW lui donne la vraie taille du contexte pour qu'il résume à temps.",
        },
        "codex": {
            "name": "Codex", "env": {"OPENAI_API_KEY": "local"}, "cmd": codex_cmd,
            "how": "Options -c en ligne de commande (rien n'est écrit dans ~/.codex/config.toml) : un fournisseur "
                   "« llamacpp » sur l'API Responses de llama-server, et le modèle local.",
        },
        "opencode": {
            "name": "OpenCode", "env": {"OPENCODE_CONFIG_CONTENT": json.dumps(opencode_cfg, ensure_ascii=False)},
            "cmd": f"opencode --model {shlex.quote('llamacpp/' + alias)}",
            "how": "Configuration passée inline (OPENCODE_CONFIG_CONTENT) : un fournisseur OpenAI-compatible sur "
                   "/v1/chat/completions, avec les modalités du modèle (images acceptées si un mmproj est chargé ; "
                   "sans cette déclaration, OpenCode remplace toute image par « this model does not support image "
                   "input »). Votre opencode.json n'est pas touché.",
        },
    }


def write_scripts(base_url, alias, ctx, proxy_url=None, vision=False):
    """Un script par outil dans ~/.ialauncher/agents/ : le lancer ouvre l'outil branché
    sur le serveur local. .sh (Linux/macOS/WSL) et .cmd (Windows)."""
    d = paths.sub("agents")
    out = {}
    for key, s in specs(base_url, alias, ctx, proxy_url, vision).items():
        sh = d / f"{key}-local.sh"
        sh.write_text("#!/usr/bin/env bash\n# Généré par IA Launcher — lance " + s["name"] +
                      " sur le serveur llama.cpp local\n" + _env_export(s["env"], "sh") + "\nexec " + s["cmd"] + ' "$@"\n')
        sh.chmod(sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        cmd = d / f"{key}-local.cmd"
        cmd.write_text("@echo off\r\nrem Généré par IA Launcher — lance " + s["name"] +
                       " sur le serveur llama.cpp local\r\n" + _env_export(s["env"], "cmd").replace("\n", "\r\n") +
                       "\r\n" + s["cmd"].replace("'", '"') + " %*\r\n")
        out[key] = {"sh": str(sh), "cmd": str(cmd)}
    return out


def open_terminal(script_sh, script_cmd, cwd=None):
    """Ouvre un terminal qui exécute le script (meilleur effort selon l'environnement)."""
    cwd = cwd or os.path.expanduser("~")
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "IA Launcher", "cmd", "/k", script_cmd], cwd=cwd)
        return "cmd"
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Terminal", script_sh])
        return "Terminal"
    # Linux : sous WSL2, Windows Terminal ; sinon les émulateurs habituels
    wt = shutil.which("wt.exe")
    if wt:
        subprocess.Popen([wt, "-w", "0", "nt", "--title", "IA Launcher", "wsl.exe", "-e", "bash", "-lc",
                          f"cd {shlex.quote(cwd)} && {shlex.quote(script_sh)}"])
        return "Windows Terminal"
    for term, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]),
                       ("xfce4-terminal", ["-x"]), ("xterm", ["-e"])):
        t = shutil.which(term)
        if t:
            subprocess.Popen([t, *args, "bash", "-lc", f"cd {shlex.quote(cwd)} && {shlex.quote(script_sh)}"])
            return term
    raise RuntimeError("aucun terminal trouvé : lancez le script vous-même")


TOOL_TEST = {
    "type": "function",
    "function": {"name": "get_weather", "description": "Donne la météo actuelle d'une ville.",
                 "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "Nom de la ville"}},
                                "required": ["city"]}},
}


def test_tool_call(base_url, timeout=120):
    """Vérifie que le modèle chargé sait appeler un outil : on lui en propose un et on
    pose une question qui l'exige. Réussite = un tool_call bien formé en réponse."""
    payload = {"messages": [{"role": "user", "content": "Quel temps fait-il à Lyon en ce moment ? Utilise l'outil."}],
               "tools": [TOOL_TEST], "tool_choice": "auto", "max_tokens": 512, "temperature": 0}
    req = urllib.request.Request(base_url + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"requête échouée : {e}"}
    msg = (d.get("choices") or [{}])[0].get("message", {})
    calls = msg.get("tool_calls") or []
    if calls:
        fn = calls[0].get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = None
        ok = fn.get("name") == "get_weather" and isinstance(args, dict) and "city" in args
        return {"ok": ok, "detail": f"appel reçu : {fn.get('name')}({fn.get('arguments')})",
                "content": msg.get("content") or ""}
    return {"ok": False, "detail": "aucun appel d'outil dans la réponse — le modèle a répondu en texte",
            "content": (msg.get("content") or "")[:400]}
