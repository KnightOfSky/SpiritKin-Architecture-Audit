using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop;

internal sealed partial class ShellInteractionController
{
    internal MenuItem AddContextMenuItem(ContextMenu menu, string header, RoutedEventHandler click)
    {
        var item = new MenuItem { Header = header };
        item.Click += click;
        menu.Items.Add(item);
        ApplyMenuItemStyle(item);
        return item;
    }

    internal Separator CreateStyledSeparator()
    {
        var separator = new Separator();
        if (TryFindResource(typeof(Separator)) is Style style)
        {
            separator.Style = style;
        }
        return separator;
    }

    internal void ApplyMenuStyle(ContextMenu menu)
    {
        if (TryFindResource(typeof(ContextMenu)) is Style style)
        {
            menu.Style = style;
        }
    }

    internal void ApplyMenuItemStyle(MenuItem item)
    {
        if (TryFindResource(typeof(MenuItem)) is Style style)
        {
            item.Style = style;
        }
    }

    internal void AddDisabledMenuHeader(ContextMenu menu, string header)
    {
        var item = new MenuItem { Header = header, IsEnabled = false };
        ApplyMenuItemStyle(item);
        menu.Items.Add(item);
    }
}
