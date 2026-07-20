using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Shapes;

namespace SpiritKinDesktop.Controls;

public partial class WorkspaceSidebarView : UserControl
{
    public WorkspaceSidebarView()
    {
        InitializeComponent();
    }

    public Button AddProjectButton => AddProjectButtonElement;

    public Button AddTaskButton => AddTaskButtonElement;

    public TextBox ApiUrlBox => ApiUrlBoxElement;

    public Button ChatsMenuButton => ChatsMenuButtonElement;

    public TextBlock ChatsSummaryText => ChatsSummaryTextElement;

    public TextBlock CollaborationChatSummaryText => CollaborationChatSummaryTextElement;

    public TextBlock CollaborationChatUnreadText => CollaborationChatUnreadTextElement;

    public StackPanel CollaborationBodyPanel => CollaborationBodyPanelElement;

    public ComboBox CollaborationContextBox => CollaborationContextBoxElement;

    public ComboBox CollaborationProjectScopeBox => CollaborationProjectScopeBoxElement;

    public ComboBox CollaborationSessionScopeBox => CollaborationSessionScopeBoxElement;

    public Button CollaborationMenuButton => CollaborationMenuButtonElement;

    public Button NewCollaborationThreadButton => NewCollaborationThreadButtonElement;

    public ListBox CollaborationThreadsList => CollaborationThreadsListElement;

    public Button ToggleCollaborationButton => ToggleCollaborationButtonElement;

    public TextBlock ConnectionStatusText => ConnectionStatusTextElement;

    public Button DeleteActiveSessionButton => DeleteActiveSessionButtonElement;

    public Button NewProjectSidebarButton => NewProjectSidebarButtonElement;

    public Button NewSessionButton => NewSessionButtonElement;

    public Button NewStandaloneSessionButton => NewStandaloneSessionButtonElement;

    public ListBox ProjectsList => ProjectsListElement;

    public Button ProjectsMenuButton => ProjectsMenuButtonElement;

    public TextBlock ProjectsSummaryText => ProjectsSummaryTextElement;

    public TextBox ProjectTitleBox => ProjectTitleBoxElement;

    public Button RefreshButton => RefreshButtonElement;

    public Button RefreshSessionsButton => RefreshSessionsButtonElement;

    public ComboBox SessionFilterBox => SessionFilterBoxElement;

    public TextBlock SessionFilterSummaryText => SessionFilterSummaryTextElement;

    public ListBox SessionsList => SessionsListElement;

    public RowDefinition SidebarListRow => SidebarListRowElement;

    public Ellipse SyncDot => SyncDotElement;

    public ListBox TasksList => TasksListElement;

    public TextBox TaskTitleBox => TaskTitleBoxElement;

    public Button ToggleChatsButton => ToggleChatsButtonElement;

    public Button ToggleProjectsButton => ToggleProjectsButtonElement;

    public PasswordBox TokenBox => TokenBoxElement;

    public ListBox WorkspaceNavList => WorkspaceNavListElement;

    public TextBlock WorkspaceRootText => WorkspaceRootTextElement;

    public TextBox WsUrlBox => WsUrlBoxElement;

    public event MouseButtonEventHandler? ProjectsListMouseLeftButtonUp;
    public event SelectionChangedEventHandler? ProjectsListSelectionChanged;
    public event MouseButtonEventHandler? CollaborationThreadsListMouseLeftButtonUp;
    public event SelectionChangedEventHandler? CollaborationThreadsListSelectionChanged;
    public event SelectionChangedEventHandler? CollaborationContextBoxSelectionChanged;
    public event SelectionChangedEventHandler? CollaborationProjectScopeBoxSelectionChanged;
    public event SelectionChangedEventHandler? CollaborationSessionScopeBoxSelectionChanged;
    public event RoutedEventHandler? CollaborationThreadArchiveMenuClick;
    public event RoutedEventHandler? CollaborationThreadCopyIdMenuClick;
    public event RoutedEventHandler? CollaborationThreadDeleteMenuClick;
    public event RoutedEventHandler? CollaborationThreadOpenManagementMenuClick;
    public event RoutedEventHandler? CollaborationThreadOpenMenuClick;
    public event SelectionChangedEventHandler? SessionsListSelectionChanged;
    public event SelectionChangedEventHandler? TasksListSelectionChanged;
    public event MouseButtonEventHandler? WorkspaceNavListMouseLeftButtonUp;

    private void CollaborationContextBox_SelectionChanged(object sender, SelectionChangedEventArgs e) => CollaborationContextBoxSelectionChanged?.Invoke(sender, e);
    private void CollaborationProjectScopeBox_SelectionChanged(object sender, SelectionChangedEventArgs e) => CollaborationProjectScopeBoxSelectionChanged?.Invoke(sender, e);
    private void CollaborationSessionScopeBox_SelectionChanged(object sender, SelectionChangedEventArgs e) => CollaborationSessionScopeBoxSelectionChanged?.Invoke(sender, e);
    private void CollaborationThreadArchiveMenu_Click(object sender, RoutedEventArgs e) => CollaborationThreadArchiveMenuClick?.Invoke(sender, e);
    private void CollaborationThreadCopyIdMenu_Click(object sender, RoutedEventArgs e) => CollaborationThreadCopyIdMenuClick?.Invoke(sender, e);
    private void CollaborationThreadDeleteMenu_Click(object sender, RoutedEventArgs e) => CollaborationThreadDeleteMenuClick?.Invoke(sender, e);
    private void CollaborationThreadOpenManagementMenu_Click(object sender, RoutedEventArgs e) => CollaborationThreadOpenManagementMenuClick?.Invoke(sender, e);
    private void CollaborationThreadOpenMenu_Click(object sender, RoutedEventArgs e) => CollaborationThreadOpenMenuClick?.Invoke(sender, e);
    private void CollaborationThreadsList_MouseLeftButtonUp(object sender, MouseButtonEventArgs e) => CollaborationThreadsListMouseLeftButtonUp?.Invoke(sender, e);
    private void CollaborationThreadsList_SelectionChanged(object sender, SelectionChangedEventArgs e) => CollaborationThreadsListSelectionChanged?.Invoke(sender, e);
    private void ProjectsList_MouseLeftButtonUp(object sender, MouseButtonEventArgs e) => ProjectsListMouseLeftButtonUp?.Invoke(sender, e);
    private void ProjectsList_SelectionChanged(object sender, SelectionChangedEventArgs e) => ProjectsListSelectionChanged?.Invoke(sender, e);
    private void SessionsList_SelectionChanged(object sender, SelectionChangedEventArgs e) => SessionsListSelectionChanged?.Invoke(sender, e);
    private void TasksList_SelectionChanged(object sender, SelectionChangedEventArgs e) => TasksListSelectionChanged?.Invoke(sender, e);
    private void WorkspaceNavList_MouseLeftButtonUp(object sender, MouseButtonEventArgs e) => WorkspaceNavListMouseLeftButtonUp?.Invoke(sender, e);
}
