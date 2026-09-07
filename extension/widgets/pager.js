export const PAGE_SIZE = 3;
export const ROTATION_SECONDS = 12;

export function pageCount(items, pageSize = PAGE_SIZE) {
    const size = Math.max(1, Math.floor(pageSize));
    return Math.max(1, Math.ceil((items?.length ?? 0) / size));
}

export function normalizePage(index, items, pageSize = PAGE_SIZE) {
    const count = pageCount(items, pageSize);
    const value = Number.isFinite(index) ? Math.floor(index) : 0;
    return ((value % count) + count) % count;
}

export function pageItems(items, index, pageSize = PAGE_SIZE) {
    const values = Array.isArray(items) ? items : [];
    const size = Math.max(1, Math.floor(pageSize));
    const page = normalizePage(index, values, size);
    const start = page * size;
    return {
        items: values.slice(start, start + size),
        page,
        pages: pageCount(values, size),
        start,
        end: Math.min(start + size, values.length),
        total: values.length,
    };
}

export function nextPage(index, items, pageSize = PAGE_SIZE) {
    return normalizePage(index + 1, items, pageSize);
}
