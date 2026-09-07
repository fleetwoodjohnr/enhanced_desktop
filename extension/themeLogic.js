/** Pure appearance helpers kept free of Shell imports for unit testing. */

/**
 * GNOME writes `prefer-dark` for Dark appearance and `default` for Light.
 * Older releases also accepted `prefer-light`; unknown values fall back to
 * the Shell variant when one is available.
 */
export function systemIsDark(colorScheme, shellVariant = null) {
    if (colorScheme === 'prefer-dark')
        return true;
    if (colorScheme === 'default' || colorScheme === 'prefer-light')
        return false;
    return shellVariant === 'dark';
}
