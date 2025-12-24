from conductor_bridge.shell_safety import decide_shell_command


def test_allows_git_status():
    d = decide_shell_command("git status")
    assert d.allowed is True


def test_blocks_compound_commands():
    d = decide_shell_command("git status; rm -rf .")
    assert d.allowed is False


def test_blocks_git_push():
    d = decide_shell_command("git push")
    assert d.allowed is False


def test_allows_get_content():
    d = decide_shell_command('Get-Content ".\\README.md"')
    assert d.allowed is True

