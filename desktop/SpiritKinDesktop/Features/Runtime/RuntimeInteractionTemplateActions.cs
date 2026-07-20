using System;
using System.Windows;
using System.Windows.Input;

namespace SpiritKinDesktop;

internal sealed partial class RuntimeController
{
    internal void HandleInteractionTemplateAction(MainWindowInteractionTemplateAction action, object sender, EventArgs args)
    {
        switch (action)
        {
            case MainWindowInteractionTemplateAction.MessageItem_MouseEnter:
                MessageItem_MouseEnter(sender, (MouseEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.MessageCopyMenu_Click:
                MessageCopyMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.MessageEditMenu_Click:
                MessageEditMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.MessageForkMenu_Click:
                MessageForkMenu_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkMessageExpander_Expanded:
                WorkMessageExpander_Expanded(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.WorkMessageExpander_Collapsed:
                WorkMessageExpander_Collapsed(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.MessageCancelEditButton_Click:
                MessageCancelEditButton_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.MessageSendEditButton_Click:
                MessageSendEditButton_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.MessageCopyButton_Click:
                MessageCopyButton_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.MessageEditButton_Click:
                MessageEditButton_Click(sender, (RoutedEventArgs)args);
                break;
            case MainWindowInteractionTemplateAction.MessageForkButton_Click:
                MessageForkButton_Click(sender, (RoutedEventArgs)args);
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(action), action, null);
        }
    }
}
