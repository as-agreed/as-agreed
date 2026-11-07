"""The README's second paragraph says "By default nothing leaves your machine". This test keeps that
checkable (`test_destination.py` checks what a remote destination is handed):
it fails if any file outside tests/ imports a module on the NETWORK list, or calls a function on
the CALLS list (they start another program or load code by name at run time, which could reach
the network without an import this test can see). It reads the code, so it catches what is
written on its lists, not every way Python can be made to reach the network."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NETWORK = {"socket", "ssl", "urllib", "http", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "telnetlib",
           "xmlrpc", "websocket", "websockets", "urllib3", "grpc", "imaplib", "poplib", "nntplib", "socketserver",
           "asyncio", "webbrowser", "subprocess", "multiprocessing", "pty", "ctypes", "importlib", "posix", "nt",
           "_posixsubprocess", "runpy", "pkgutil", "zipimport", "concurrent", "logging.handlers"}
CALLS = {"os.system", "os.popen", "os.fork", "os.forkpty", "os.posix_spawn", "os.posix_spawnp", "os.*",
         "import os as", "__import__", "eval", "exec", "compile"}
CALL_PREFIXES = ("os.exec", "os.spawn")


def shipped_files():
    for path in sorted(ROOT.rglob("*.py")):
        if "tests" not in path.relative_to(ROOT).parts:
            yield path


def imported_modules(tree):
    """Each imported module by its first part and by its full dotted name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0]
            yield node.module
            for alias in node.names:
                yield f"{node.module}.{alias.name}"


def called_names(tree):
    """Dotted names of what is called: `os.system(...)` gives "os.system", `eval(...)` gives "eval".
    Also counted as calls: `from os import <name>`, `import os as <alias>`, and getattr with a
    written name, `getattr(os, "system")`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parts, f = [], node.func
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                yield ".".join([f.id] + parts[::-1])
                if f.id == "getattr" and not parts and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                        and isinstance(node.args[1].value, str):
                    yield "os." + node.args[1].value
        elif isinstance(node, ast.ImportFrom) and node.module == "os" and node.level == 0:
            for alias in node.names:
                yield "os." + alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" and alias.asname:
                    yield "import os as"


class NothingLeaves(unittest.TestCase):
    def test_no_shipped_file_imports_a_network_module(self):
        found = {}
        for p in shipped_files():
            hits = sorted(set(imported_modules(ast.parse(p.read_text(encoding="utf-8")))) & NETWORK)
            if hits:
                found[str(p.relative_to(ROOT))] = hits
        self.assertEqual(found, {})

    def test_no_shipped_file_starts_a_program_or_loads_code_by_name(self):
        found = {}
        for p in shipped_files():
            hits = sorted({c for c in called_names(ast.parse(p.read_text(encoding="utf-8")))
                           if c in CALLS or c.startswith(CALL_PREFIXES)})
            if hits:
                found[str(p.relative_to(ROOT))] = hits
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
