import Clutter from 'gi://Clutter';
import St from 'gi://St';

/** Keep the native scroll/drag mechanics, with explicit card-hover visibility. */
export function styleScrollbar(widget, scroll) {
    scroll.overlay_scrollbars = true;
    widget.track_hover = true;
    const bars = scroll.get_children().filter(actor => actor instanceof St.ScrollBar);
    for (const bar of bars) {
        const vertical = bar.get_adjustment() === scroll.get_vadjustment();
        if (!vertical)
            continue;
        // ScrollView paints overlay bars last, but its content was appended
        // after them. Match the input order to the paint order as well.
        scroll.set_child_above_sibling(bar, null);
        bar.set_style('min-width: 10px; padding: 0; background-color: transparent;');
        for (const child of bar.get_children()) {
            if (child.name === 'vhandle' || child.has_style_class_name?.('vhandle')) {
                const color = widget._style.dark ? 'rgba(247,250,255,0.40)' : 'rgba(23,32,51,0.38)';
                // St paints backgrounds underneath transparent borders. Draw
                // a separate 4px line inside the wider native drag target.
                child.set_style('background-color: transparent; border: none; padding: 0; box-shadow: none;');
                child.layout_manager = new Clutter.BinLayout();
                child.add_child(new St.Widget({
                    style_class: 'df-scroll-thumb',
                    reactive: false,
                    x_align: Clutter.ActorAlign.CENTER,
                    y_expand: true,
                    style: `width: 4px; border-radius: 2px; background-color: ${color};`,
                }));
            } else if (child.name === 'trough') {
                child.set_style('background-color: transparent; border: none; box-shadow: none;');
            }
        }
        bar.opacity = 0;
        const sync = () => {
            bar.ease({
                opacity: widget.mapped && (widget.hover || widget._scrollbarDragging) ? 255 : 0,
                duration: 150,
                mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            });
        };
        widget.connect('notify::hover', sync);
        widget.connect('notify::mapped', sync);
        bar.connectObject('scroll-start', () => {
            widget._scrollbarDragging = true;
            sync();
            widget._interactionChanged?.();
        }, 'scroll-stop', () => {
            widget._scrollbarDragging = false;
            sync();
            widget._interactionChanged?.();
        }, widget);
    }
}
