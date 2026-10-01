import launchers


def test_only_what_is_installed_is_offered(tmp_path):
    apps = [{"Name": "Claude", "AppID": "Claude_x!Claude"}, {"Name": "Notepad", "AppID": "n"}]
    (tmp_path / ".vscode" / "extensions" / "anthropic.claude-code-2.1.0-win32-x64").mkdir(parents=True)
    claude = launchers.options("claude", apps, tmp_path, which=lambda name: None)
    assert [o["type"] for o in claude] == ["shell", "uri", "url"]
    assert claude[1]["target"].startswith("vscode://anthropic.claude-code")
    openai = launchers.options("openai", apps, tmp_path, which=lambda name: "C:/codex.cmd" if name == "codex" else None)
    assert [o["type"] for o in openai] == ["terminal", "url"]          # no ChatGPT app, no extension: terminal and web remain


def test_the_store_version_of_chatgpt_is_recognised(tmp_path):
    apps = [{"Name": "ChatGPT", "AppID": "OpenAI.Codex_abc!App"}]
    assert launchers.options("openai", apps, tmp_path, which=lambda name: None)[0]["target"] == "OpenAI.Codex_abc!App"
    assert [o["type"] for o in launchers.options("openai", [{"Name": "ChatGPT", "AppID": "Other.App!x"}], tmp_path, which=lambda n: None)] == ["url"]


def test_unknown_products_have_no_menu():
    assert launchers.options("paperclip", []) == []
