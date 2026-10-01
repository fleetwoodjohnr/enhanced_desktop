// Prints the extension's chrome defaults so tests/test_config.py can compare
// them with desktop_forge/config.py.
import {CHROME_DEFAULTS} from '../extension/chromeLogic.js';

print(JSON.stringify(CHROME_DEFAULTS));
