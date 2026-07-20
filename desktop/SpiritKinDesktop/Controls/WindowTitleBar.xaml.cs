using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Media3D;

namespace SpiritKinDesktop.Controls;

public partial class WindowTitleBar : UserControl
{
    public event EventHandler? DragRequested;
    public event EventHandler? MaximizeRestoreRequested;
    public event EventHandler? ThemeToggleRequested;

    public WindowTitleBar()
    {
        InitializeComponent();
    }

    public WindowAppMenu AppMenu => AppMenuControl;

    public WindowCaptionButtons CaptionButtons => CaptionButtonsControl;

    public void SetThemeToggleState(bool isDark) => ThemeToggleButtonElement.Content = isDark ? "夜间" : "日间";

    private void ThemeToggleButton_Click(object sender, RoutedEventArgs e) => ThemeToggleRequested?.Invoke(this, EventArgs.Empty);

    private void TitleBar_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        var source = e.OriginalSource as DependencyObject;
        if (IsWithin<Menu>(source) || IsWithin<Button>(source))
        {
            return;
        }

        if (e.ClickCount == 2)
        {
            MaximizeRestoreRequested?.Invoke(this, EventArgs.Empty);
            return;
        }

        if (e.ButtonState == MouseButtonState.Pressed)
        {
            DragRequested?.Invoke(this, EventArgs.Empty);
        }
    }

    private static bool IsWithin<T>(DependencyObject? source) where T : DependencyObject
    {
        while (source is not null)
        {
            if (source is T)
            {
                return true;
            }

            source = source is Visual || source is Visual3D
                ? VisualTreeHelper.GetParent(source)
                : LogicalTreeHelper.GetParent(source);
        }

        return false;
    }
}
