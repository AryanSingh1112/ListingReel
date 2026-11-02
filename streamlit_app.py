import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
os.environ["LISTINGREEL_NO_MAIN"] = "1"  # we call main() ourselves on every rerun

from listingreel import app  # noqa: E402

app.main()
