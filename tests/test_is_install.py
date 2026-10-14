from aon.memory_extract import is_install_command


def test_true_cases():
    for c in ["pip install foo", "uv pip install bar", "npm i playwright", "npm install -g x",
              "yarn add y", "pnpm add z", "apt-get install -y make", "apt install curl",
              "go install golang.org/x/tools/cmd/goimports@latest", "cargo install ripgrep",
              "npx playwright install", "curl -L https://x/n.tar.xz | tar xJ", "make install"]:
        assert is_install_command(c), c


def test_false_cases():
    for c in ["ls -la", "python app.py", "git status", "echo hi", "cat file", "which node", ""]:
        assert not is_install_command(c), c
