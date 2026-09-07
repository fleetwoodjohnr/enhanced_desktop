import GObject from 'gi://GObject';
import St from 'gi://St';

import {DesktopWidget} from './base.js';

export const CalendarWidget = GObject.registerClass(
class CalendarWidget extends DesktopWidget {
    buildBody(body) {
        this.addTitle(body, 'Upcoming', 'x-office-calendar-symbolic');
        this._rows = new St.BoxLayout({
            style_class: 'df-list', vertical: true, x_expand: true,
        });
        body.add_child(this._rows);
    }

    render(data) {
        this._rows.destroy_all_children();

        const events = data.events ?? [];
        if (!events.length) {
            this._rows.add_child(new St.Label({
                style_class: 'df-empty', text: emptyReason(data),
            }));
            return;
        }

        for (const event of events) {
            const row = new St.BoxLayout({
                style_class: 'df-row df-calendar-row', x_expand: true,
            });
            const when = new St.Label({
                style_class: 'df-cal-date',
                text: formatWhen(event.start, event.all_day),
            });
            when.set_style([
                `color: ${this.accent}`,
                `background-color: ${this.surface}`,
            ].join('; '));
            row.add_child(when);

            const summary = new St.Label({
                style_class: 'df-cal-summary', text: event.summary ?? '', x_expand: true,
            });
            summary.clutter_text.ellipsize = 3; // Pango.EllipsizeMode.END
            row.add_child(summary);

            this._rows.add_child(row);
        }

        // A calendar that failed while others answered still has to say so,
        // or the list silently looks complete.
        const [problem] = data.problems ?? [];
        if (problem) {
            this._rows.add_child(new St.Label({
                style_class: 'df-error', text: problem,
            }));
        }
    }
});

/**
 * Why the list is empty, which is rarely "nothing is scheduled".
 *
 * Saying whether no source was discovered or simply no event is due is the
 * difference between a widget that looks broken and one that tells the user
 * where to go.
 */
function emptyReason(data) {
    const [problem] = data.problems ?? [];
    if (problem)
        return problem;
    // Older daemons did not report the calendar list; treat missing as unknown
    // rather than as none, so an upgrade in progress doesn't lie.
    if (Array.isArray(data.calendars) && !data.calendars.length)
        return 'No calendars — add one in Online Accounts or Thunderbird';
    return `Nothing in the next ${data.days_ahead ?? 14} days`;
}

function formatWhen(iso, allDay) {
    if (!iso)
        return '';
    const date = new Date(allDay ? `${iso}T00:00:00` : iso);
    if (isNaN(date))
        return '';

    const day = date.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
    if (allDay)
        return day;
    const time = date.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'});
    return `${day} ${time}`;
}
