using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class ShellInteractionController
{
    internal static bool IsSubmitKey(Key key) => key is Key.Enter or Key.Return;

    internal static bool IsTextInputKey(Key key)
    {
        if (key is Key.Space or Key.OemPlus or Key.OemComma or Key.OemMinus or Key.OemPeriod or Key.OemQuestion or Key.Oem1 or Key.Oem2 or Key.Oem3 or Key.Oem4 or Key.Oem5 or Key.Oem6 or Key.Oem7)
        {
            return true;
        }
        return (key >= Key.A && key <= Key.Z) || (key >= Key.D0 && key <= Key.D9) || (key >= Key.NumPad0 && key <= Key.NumPad9);
    }

    internal static bool IsWithin<T>(DependencyObject? source) where T : DependencyObject
    {
        while (source is not null)
        {
            if (source is T)
            {
                return true;
            }
            source = VisualTreeHelper.GetParent(source) ?? LogicalTreeHelper.GetParent(source);
        }
        return false;
    }

    private Control? CurrentTextEditTarget()
    {
        if (Keyboard.FocusedElement is TextBox textBox)
        {
            _lastTextEditTarget = textBox;
            return textBox;
        }
        if (Keyboard.FocusedElement is PasswordBox passwordBox)
        {
            _lastTextEditTarget = passwordBox;
            return passwordBox;
        }
        return _lastTextEditTarget;
    }

    internal void ExecuteTextEditAction(string action, Control? explicitTarget = null)
    {
        var target = explicitTarget ?? CurrentTextEditTarget();
        if (target is TextBox textBox)
        {
            textBox.Focus();
            switch (action)
            {
                case "undo":
                    if (textBox.CanUndo && !textBox.IsReadOnly)
                    {
                        textBox.Undo();
                    }
                    break;
                case "cut":
                    if (!textBox.IsReadOnly)
                    {
                        textBox.Cut();
                    }
                    break;
                case "copy":
                    textBox.Copy();
                    break;
                case "paste":
                    if (!textBox.IsReadOnly)
                    {
                        textBox.Paste();
                    }
                    break;
                case "select_all":
                    textBox.SelectAll();
                    break;
            }
            return;
        }

        if (target is PasswordBox passwordBox)
        {
            passwordBox.Focus();
            switch (action)
            {
                case "paste":
                    passwordBox.Paste();
                    break;
                case "select_all":
                    passwordBox.SelectAll();
                    break;
            }
        }
    }

    internal void TextEditTarget_GotKeyboardFocus(object sender, KeyboardFocusChangedEventArgs e)
    {
        if (e.OriginalSource is TextBox textBox)
        {
            _lastTextEditTarget = textBox;
            EnsureTextEditContextMenu(textBox);
        }
        else if (e.OriginalSource is PasswordBox passwordBox)
        {
            _lastTextEditTarget = passwordBox;
            EnsureTextEditContextMenu(passwordBox);
        }
    }

    internal void TextEditTarget_ContextMenuOpening(object sender, ContextMenuEventArgs e)
    {
        if (e.OriginalSource is TextBox textBox)
        {
            _lastTextEditTarget = textBox;
            EnsureTextEditContextMenu(textBox);
        }
        else if (e.OriginalSource is PasswordBox passwordBox)
        {
            _lastTextEditTarget = passwordBox;
            EnsureTextEditContextMenu(passwordBox);
        }
    }

    internal void InstallTextEditContextMenus(DependencyObject root)
    {
        if (root is TextBox textBox)
        {
            EnsureTextEditContextMenu(textBox);
        }
        else if (root is PasswordBox passwordBox)
        {
            EnsureTextEditContextMenu(passwordBox);
        }

        var count = VisualTreeHelper.GetChildrenCount(root);
        for (var i = 0; i < count; i++)
        {
            InstallTextEditContextMenus(VisualTreeHelper.GetChild(root, i));
        }
    }

    internal void EnsureTextEditContextMenu(TextBox textBox)
    {
        textBox.ContextMenu ??= BuildTextEditContextMenu(textBox);
    }

    private void EnsureTextEditContextMenu(PasswordBox passwordBox)
    {
        passwordBox.ContextMenu ??= BuildTextEditContextMenu(passwordBox);
    }

    private ContextMenu BuildTextEditContextMenu(Control target)
    {
        var menu = new ContextMenu { PlacementTarget = target };
        ApplyMenuStyle(menu);

        var undoItem = AddTextEditMenuItem(menu, "撤销", (_, _) => ExecuteTextEditAction("undo", target));
        menu.Items.Add(CreateStyledSeparator());
        var cutItem = AddTextEditMenuItem(menu, "剪切", (_, _) => ExecuteTextEditAction("cut", target));
        var copyItem = AddTextEditMenuItem(menu, "复制", (_, _) => ExecuteTextEditAction("copy", target));
        var pasteItem = AddTextEditMenuItem(menu, "粘贴", (_, _) => ExecuteTextEditAction("paste", target));
        menu.Items.Add(CreateStyledSeparator());
        var selectAllItem = AddTextEditMenuItem(menu, "全选", (_, _) => ExecuteTextEditAction("select_all", target));

        menu.Opened += (_, _) =>
        {
            _lastTextEditTarget = target;
            if (target is TextBox textBox)
            {
                undoItem.IsEnabled = textBox.CanUndo && !textBox.IsReadOnly;
                cutItem.IsEnabled = !textBox.IsReadOnly && textBox.SelectionLength > 0;
                copyItem.IsEnabled = textBox.SelectionLength > 0;
                pasteItem.IsEnabled = !textBox.IsReadOnly && Clipboard.ContainsText();
                selectAllItem.IsEnabled = textBox.Text.Length > 0;
                return;
            }

            if (target is PasswordBox passwordBox)
            {
                undoItem.IsEnabled = false;
                cutItem.IsEnabled = false;
                copyItem.IsEnabled = false;
                pasteItem.IsEnabled = Clipboard.ContainsText();
                selectAllItem.IsEnabled = passwordBox.Password.Length > 0;
            }
        };

        return menu;
    }

    private MenuItem AddTextEditMenuItem(ContextMenu menu, string header, RoutedEventHandler click)
    {
        var item = new MenuItem { Header = header };
        ApplyMenuItemStyle(item);
        item.Click += click;
        menu.Items.Add(item);
        return item;
    }
}
