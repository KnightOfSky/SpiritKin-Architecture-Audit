using System;
using System.Windows;
using System.Windows.Controls;

namespace SpiritKinDesktop.Controls;

public partial class WindowCaptionButtons : UserControl
{
    public event EventHandler? MinimizeRequested;
    public event EventHandler? MaximizeRestoreRequested;
    public event EventHandler? CloseRequested;

    public WindowCaptionButtons()
    {
        InitializeComponent();
    }

    public void SetMaximized(bool isMaximized)
    {
        MaximizeRestoreButton.Content = isMaximized ? "[]" : "[ ]";
    }

    private void MinimizeButton_Click(object sender, RoutedEventArgs e)
    {
        MinimizeRequested?.Invoke(this, EventArgs.Empty);
    }

    private void MaximizeRestoreButton_Click(object sender, RoutedEventArgs e)
    {
        MaximizeRestoreRequested?.Invoke(this, EventArgs.Empty);
    }

    private void CloseButton_Click(object sender, RoutedEventArgs e)
    {
        CloseRequested?.Invoke(this, EventArgs.Empty);
    }
}
