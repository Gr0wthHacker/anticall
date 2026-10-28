"""`python -m callshield.telephony.asterisk` starts the long-running telephony runner."""

import sys

from callshield.telephony.asterisk.runner import main

if __name__ == "__main__":
    sys.exit(main())
