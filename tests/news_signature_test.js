import {newsRequestSignature} from '../extension/widgetLogic.js';

print(JSON.stringify(JSON.parse(ARGV[0]).map(newsRequestSignature)));
