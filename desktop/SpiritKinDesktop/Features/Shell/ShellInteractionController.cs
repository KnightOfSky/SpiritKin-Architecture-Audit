using System;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class ShellInteractionController
{
    private readonly Func<object, object?> _tryFindResource;
    private Control? _lastTextEditTarget;

    internal ShellInteractionController(Func<object, object?> tryFindResource)
    {
        _tryFindResource = tryFindResource;
    }

    private object? TryFindResource(object key) => _tryFindResource(key);
}
