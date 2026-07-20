using System.Windows.Controls;
using System.Windows.Input;

namespace SpiritKinDesktop.Controls;

public partial class GlobalSearchOverlayView : UserControl
{
    public GlobalSearchOverlayView()
    {
        InitializeComponent();
    }

    public Button GlobalSearchCloseButton => GlobalSearchCloseButtonElement;

    public TextBlock GlobalSearchSummaryText => GlobalSearchSummaryTextElement;

    public TextBox GlobalSearchBox => GlobalSearchBoxElement;

    public TextBlock GlobalSearchEmptyText => GlobalSearchEmptyTextElement;

    public ListBox GlobalSearchResultsList => GlobalSearchResultsListElement;

    public event MouseButtonEventHandler? OverlayMouseDown;

    public event MouseButtonEventHandler? PanelMouseDown;

    public event TextChangedEventHandler? SearchTextChanged;

    public event KeyEventHandler? SearchPreviewKeyDown;

    public event MouseButtonEventHandler? ResultsMouseDoubleClick;

    public event KeyEventHandler? ResultsPreviewKeyDown;

    private void OverlayElement_MouseDown(object sender, MouseButtonEventArgs e)
    {
        OverlayMouseDown?.Invoke(sender, e);
    }

    private void PanelElement_MouseDown(object sender, MouseButtonEventArgs e)
    {
        PanelMouseDown?.Invoke(sender, e);
    }

    private void GlobalSearchBoxElement_TextChanged(object sender, TextChangedEventArgs e)
    {
        SearchTextChanged?.Invoke(sender, e);
    }

    private void GlobalSearchBoxElement_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        SearchPreviewKeyDown?.Invoke(sender, e);
    }

    private void GlobalSearchResultsListElement_MouseDoubleClick(object sender, MouseButtonEventArgs e)
    {
        ResultsMouseDoubleClick?.Invoke(sender, e);
    }

    private void GlobalSearchResultsListElement_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        ResultsPreviewKeyDown?.Invoke(sender, e);
    }
}
