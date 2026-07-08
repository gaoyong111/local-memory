"""Claude Code UserPromptSubmit hook — 委托给 memory_hook"""

import sys

sys.argv = [sys.argv[0], '--format', 'claude']

from memory_hook import main

if __name__ == '__main__':
    main()
