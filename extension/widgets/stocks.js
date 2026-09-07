import GObject from 'gi://GObject';
import St from 'gi://St';

import {ScrollableWidget} from './scrollable.js';

export const StocksWidget = GObject.registerClass(
class StocksWidget extends ScrollableWidget {
    buildBody(body) {
        this._quotes = [];
        this.initFeed(body, 'Markets', 'x-office-spreadsheet-symbolic', 'df-list');
    }

    render(data) {
        this._quotes = Array.isArray(data.quotes) ? data.quotes : [];
        this.renderItems(this._quotes, quote => quote.symbol, quote => {
            const row = new St.BoxLayout({
                style_class: 'df-row df-stock-row', x_expand: true,
            });
            row.add_child(new St.Label({
                style_class: 'df-stock-symbol', text: quote.symbol, style: 'width: 68px',
            }));
            row.add_child(new St.Label({
                text: quote.price != null ? formatPrice(quote.price) : '--',
                x_expand: true, style: 'text-align: right',
            }));
            const percent = quote.change_percent;
            row.add_child(new St.Label({
                style_class: percent == null ? 'df-change' :
                    `df-change ${percent >= 0 ? 'df-change-up' : 'df-change-down'}`,
                text: percent == null ? '' : `${percent >= 0 ? '▲' : '▼'} ${Math.abs(percent).toFixed(2)}%`,
                style: 'width: 78px; text-align: right',
            }));
            return row;
        }, 'No symbols configured');
    }
});

function formatPrice(value) {
    const digits = Math.abs(value) < 1 ? 4 : 2;
    return value.toFixed(digits);
}
