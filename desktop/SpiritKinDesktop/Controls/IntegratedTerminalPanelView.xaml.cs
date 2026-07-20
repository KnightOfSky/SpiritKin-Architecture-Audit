using System.Windows.Controls;

namespace SpiritKinDesktop.Controls;

public partial class IntegratedTerminalPanelView : UserControl
{
    public IntegratedTerminalPanelView()
    {
        InitializeComponent();
    }

    public Button CloseTerminalButton => CloseTerminalButtonElement;

    public TextBlock TerminalTitleText => TerminalTitleTextElement;

    public TextBox TerminalOutputBox => TerminalOutputBoxElement;
}
