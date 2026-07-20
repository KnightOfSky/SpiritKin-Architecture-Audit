using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    private async void MainWindow_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (GlobalSearchOverlay.Visibility == Visibility.Visible)
        {
            if (e.Key == Key.Escape)
            {
                _globalSearchController.CloseGlobalSearch();
                e.Handled = true;
            }
            return;
        }
        if ((Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control && e.Key == Key.F)
        {
            _globalSearchController.OpenGlobalSearch();
            e.Handled = true;
            return;
        }
        if ((Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control && e.Key == Key.Z)
        {
            if (IsTextInputFocused())
            {
                _shellInteractionController.ExecuteTextEditAction("undo");
                e.Handled = true;
                return;
            }
            if (WorkbenchShell.ManagementPanels.WorkflowsPanel.Visibility == Visibility.Visible)
            {
                _workflowController.UndoWorkflowEditor();
                e.Handled = true;
                return;
            }
        }
        if (e.Key == Key.Delete && !IsTextInputFocused())
        {
            await _navigationController.DeleteCurrentSelectionAsync();
            e.Handled = true;
            return;
        }
        if (WorkbenchShell.ManagementPanels.WorkflowsPanel.Visibility != Visibility.Visible || IsTextInputFocused())
        {
            return;
        }
        if ((Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control && e.Key == Key.Y)
        {
            _workflowController.RedoWorkflowEditor();
            e.Handled = true;
        }
        else if ((Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control && e.Key == Key.A)
        {
            _workflowController.SelectAllWorkflowGraphNodes();
            e.Handled = true;
        }
        else if (e.Key == Key.Escape)
        {
            _workflowController.CancelWorkflowGraphInteraction();
            e.Handled = true;
        }
    }

    private static bool IsTextInputFocused()
    {
        return Keyboard.FocusedElement is TextBoxBase or PasswordBox or ComboBox;
    }

}
