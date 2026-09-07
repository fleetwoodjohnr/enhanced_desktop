import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {newsRequestSignature, NEWS_TOPIC_IDS} from '../widgetLogic.js';
import {ScrollableWidget} from './scrollable.js';
import {HeadlineTicker} from './headlineTicker.js';

Gio._promisify(Gio.AppInfo, 'launch_default_for_uri_async');

export const NewsWidget = GObject.registerClass(
class NewsWidget extends ScrollableWidget {
    buildBody(body) {
        this._headlines = [];
        this._headlineTickers = [];
        this._filterSignature = newsRequestSignature();
        this._topicsDisabled = false;
        this.initFeed(body, 'News', 'application-rss+xml-symbolic', 'df-news-list');
        this._problem = new St.Label({style_class: 'df-error'});
        this._problem.hide();
        body.add_child(this._problem);
    }

    setNewsFilter(options = {}) {
        const signature = newsRequestSignature(options);
        if (signature === this._filterSignature)
            return;
        this._filterSignature = signature;
        this._topicsDisabled = Array.isArray(options.topic_presets) &&
            !options.topic_presets.some(id => NEWS_TOPIC_IDS.includes(String(id).trim().toLowerCase()));
        this._clearFeed();
    }

    _clearFeed() {
        this._pendingData = null;
        this._dataSignature = null;
        this._headlines = [];
        this._headlineTickers = [];
        this._pendingAnchor = {value: 0, offset: 0};
        this._problem.hide();
        this.renderItems([], () => '', () => null,
            this._topicsDisabled ? 'No topics selected' : 'Updating news…');
    }

    update(state) {
        // Last-good data may still belong to the previously selected topics.
        if (state?.data?.request_signature !== this._filterSignature) {
            if (this._headlines.length || this._dataSignature || !this._rows.get_n_children())
                this._clearFeed();
            this.setStatus(state && !state.ok ? state.error ?? 'Unable to update news' : null, false);
            return;
        }
        super.update(state);
    }

    render(data) {
        this._headlines = Array.isArray(data.headlines) ? data.headlines : [];
        const topics = Array.isArray(data.topics) ? data.topics : [];
        const problems = data.problems ?? [];
        const problem = problems.join('; ');
        const noSources = Array.isArray(data.sources) && !data.sources.length;
        const emptyText = data.topics_disabled ? 'No topics selected' : noSources ? 'No feeds configured' :
            (topics.length ? `No headlines match ${topics.join(', ')}` :
                (problem ?? 'No headlines available'));
        this._problem.text = problem ? `${problems.length} feed${problems.length === 1 ? '' : 's'} unavailable · ${problem}` : '';
        this._problem.clutter_text.line_wrap = true;
        this._problem.visible = !!problem;
        this._headlineTickers = [];
        this.renderItems(this._headlines,
            story => story.url ?? `${story.source}:${story.title}`,
            (story, offset) => this._makeRow(story, offset), emptyText);
    }

    _makeRow(story, offset) {
        const index = new St.Label({
            style_class: 'df-news-index', text: `${offset + 1}`,
            y_align: Clutter.ActorAlign.CENTER,
            style: `color: ${this.accent}; background-color: ${this.surface}`,
        });
        const content = new St.BoxLayout({
            vertical: true, x_expand: true, x_align: Clutter.ActorAlign.FILL,
            clip_to_allocation: true,
        });
        const headline = new HeadlineTicker(story.title ?? '');
        this._headlineTickers.push(headline);
        content.add_child(headline);
        content.add_child(new St.Label({
            style_class: 'df-news-meta',
            text: [story.source, relativeTime(story.published)].filter(Boolean).join(' · '),
            x_align: Clutter.ActorAlign.START, style: `color: ${this.muted}`,
        }));
        const rowContent = new St.BoxLayout({
            style_class: 'df-news-content', x_expand: true,
            x_align: Clutter.ActorAlign.FILL, clip_to_allocation: true,
        });
        rowContent.add_child(index);
        rowContent.add_child(content);
        const row = new St.Button({
            style_class: 'df-news-item', child: rowContent,
            x_expand: true, x_align: Clutter.ActorAlign.FILL,
            clip_to_allocation: true, can_focus: true,
            accessible_name: `Open ${story.title ?? 'news story'}`,
        });
        row.connect('notify::hover', () => this._syncHoveredRows());
        row.connect('clicked', () => this._openStory(story.url));
        return row;
    }

    _syncHoveredRows(enabled = true) {
        if (!this._scroll || this._destroying)
            return;
        if (!this.mapped) {
            for (const ticker of this._headlineTickers)
                ticker.setHovered(false);
            return;
        }
        const [x, y] = global.get_pointer();
        const [sx, sy] = this._scroll.get_transformed_position();
        const [sw, sh] = this._scroll.get_transformed_size();
        const inside = enabled && !this._settleId && this.hover && this.mapped &&
            x >= sx && x < sx + sw && y >= sy && y < sy + sh;
        this._headlineTickers.forEach((ticker, i) => {
            const row = this._feedEntries[i]?.row;
            let hovered = false;
            if (inside && row?.mapped) {
                const [rx, ry] = row.get_transformed_position();
                const [rw, rh] = row.get_transformed_size();
                hovered = x >= rx && x < rx + rw && y >= ry && y < ry + rh;
            }
            ticker.setHovered(hovered);
        });
    }

    async _openStory(url) {
        if (!/^https?:\/\//i.test(url ?? ''))
            return;
        const context = global.create_app_launch_context(global.get_current_time(), -1);
        try {
            await Gio.AppInfo.launch_default_for_uri_async(url, context, null);
        } catch (error) {
            Main.notifyError('Could not open news story', error.message);
        }
    }
});

function relativeTime(iso) {
    if (!iso)
        return '';
    const published = new Date(iso);
    if (isNaN(published))
        return '';
    const seconds = Math.max(0, Math.floor((Date.now() - published.getTime()) / 1000));
    if (seconds < 60)
        return 'now';
    if (seconds < 3600)
        return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400)
        return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800)
        return `${Math.floor(seconds / 86400)}d ago`;
    return published.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
}
