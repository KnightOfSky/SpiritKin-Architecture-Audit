using System;
using System.Diagnostics;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;

namespace SpiritKinDesktop.Controls;

public partial class WorkbenchShellView : UserControl
{
    private EventHandler? _workbenchLayoutTick;
    private DispatcherTimer? _workbenchLayoutTimer;

    public WorkbenchShellView()
    {
        InitializeComponent();
        Loaded += (_, _) =>
        {
            WorkbenchStatusScrollViewerElement.ScrollToTop();
            StartAmbientMotion();
        };
        Unloaded += (_, _) =>
        {
            StopAmbientMotion();
            StopWorkbenchLayoutAnimation();
        };
        WorkbenchProgressTrackElement.SizeChanged += (_, _) =>
        {
            if (IsLoaded)
            {
                WorkbenchStatusScrollViewerElement.ScrollToTop();
                StartAmbientMotion();
            }
        };
    }

    public System.Windows.Controls.Border AvatarPanel => AvatarPanelElement;

    public System.Windows.Controls.RowDefinition AvatarPanelRow => AvatarPanelRowElement;

    public System.Windows.Controls.GridSplitter AvatarPanelSplitter => AvatarPanelSplitterElement;

    public System.Windows.Controls.RowDefinition AvatarSplitterRow => AvatarSplitterRowElement;

    public Microsoft.Web.WebView2.Wpf.WebView2 AvatarView => AvatarViewElement;

    public async Task PrepareAvatarLayoutAnimationAsync()
    {
        StopWorkbenchLayoutAnimation();
        AvatarAnimationProxyElement.Source = null;
        AvatarAnimationProxyElement.Visibility = Visibility.Collapsed;

        var proxyReady = false;

        try
        {
            if (AvatarViewElement.CoreWebView2 is not null
                && AvatarViewElement.ActualWidth > 1
                && AvatarViewElement.ActualHeight > 1)
            {
                using var stream = new MemoryStream();
                await AvatarViewElement.CoreWebView2.CapturePreviewAsync(
                    CoreWebView2CapturePreviewImageFormat.Png,
                    stream);
                stream.Position = 0;
                var snapshot = new BitmapImage();
                snapshot.BeginInit();
                snapshot.CacheOption = BitmapCacheOption.OnLoad;
                snapshot.StreamSource = stream;
                snapshot.EndInit();
                snapshot.Freeze();
                AvatarAnimationProxyElement.Source = snapshot;
                AvatarAnimationProxyElement.Visibility = Visibility.Visible;
                proxyReady = true;
            }
        }
        catch (Exception)
        {
            // Keep the live WebView visible when a transition snapshot cannot be captured.
        }

        AvatarViewElement.Visibility = proxyReady ? Visibility.Hidden : Visibility.Visible;
    }

    public void RestoreAvatarAfterLayoutAnimation()
    {
        AvatarViewElement.Visibility = Visibility.Visible;
        AvatarAnimationProxyElement.Visibility = Visibility.Collapsed;
        AvatarAnimationProxyElement.Source = null;
    }

    public System.Windows.Controls.Button BranchEnvironmentButton => BranchEnvironmentButtonElement;

    public System.Windows.Controls.TextBlock BranchEnvironmentText => BranchEnvironmentTextElement;

    public System.Windows.Controls.TextBox ChangedFileDiffBox => ChangedFileDiffBoxElement;

    public System.Windows.Controls.Button CollapseWorkbenchPanelButton => CollapseWorkbenchPanelButtonElement;

    public System.Windows.Controls.Border WorkbenchRestoreBar => WorkbenchRestoreBarElement;

    public System.Windows.Controls.Button RestoreWorkbenchPanelButton => RestoreWorkbenchPanelButtonElement;

    public System.Windows.Documents.Run InstrumentChangesRun => InstrumentChangesRunElement;

    public System.Windows.Documents.Run InstrumentBranchRun => InstrumentBranchRunElement;

    public System.Windows.Documents.Run InstrumentGhRun => InstrumentGhRunElement;

    public System.Windows.Controls.Button CommitPushButton => CommitPushButtonElement;

    public System.Windows.Controls.Expander EnvironmentExpander => EnvironmentExpanderElement;

    public System.Windows.Controls.TextBlock EnvironmentStatusText => EnvironmentStatusTextElement;

    public System.Windows.Controls.ListBox GitChangesList => GitChangesListElement;

    public System.Windows.Controls.TextBlock GitHubCliStatusText => GitHubCliStatusTextElement;

    public System.Windows.Controls.Button LocalEnvironmentButton => LocalEnvironmentButtonElement;

    public System.Windows.Controls.TextBlock LocalEnvironmentText => LocalEnvironmentTextElement;

    public System.Windows.Controls.Grid ManagementPanelHost => ManagementPanelHostElement;

    public SpiritKinDesktop.Controls.ManagementPanelsView ManagementPanels => ManagementPanelsElement;

    public System.Windows.Controls.Button ManageActiveWorkspaceButton => ManageActiveWorkspaceButtonElement;

    public System.Windows.Controls.Button OpenAvatarButton => OpenAvatarButtonElement;

    public System.Windows.Controls.Button OpenActiveWorkspaceButton => OpenActiveWorkspaceButtonElement;

    public System.Windows.Controls.Button OpenTerminalButton => OpenTerminalButtonElement;

    public System.Windows.Controls.Expander ProgressExpander => ProgressExpanderElement;

    public System.Windows.Controls.Button RefreshGitChangesButton => RefreshGitChangesButtonElement;

    public System.Windows.Controls.Button ReloadAvatarButton => ReloadAvatarButtonElement;

    public System.Windows.Controls.Button ReviewChangesButton => ReviewChangesButtonElement;

    public System.Windows.Controls.TextBlock RightModuleTitleText => RightModuleTitleTextElement;

    public System.Windows.Controls.ColumnDefinition RightNavColumn => RightNavColumnElement;

    public System.Windows.Controls.StackPanel RightNavHeaderPanel => RightNavHeaderPanelElement;

    public System.Windows.Controls.ListBox RightNavList => RightNavListElement;

    public System.Windows.Controls.TextBlock SourcesEmptyText => SourcesEmptyTextElement;

    public System.Windows.Controls.Expander SourcesExpander => SourcesExpanderElement;

    public System.Windows.Controls.ListBox SourcesList => SourcesListElement;

    public System.Windows.Controls.Button ToggleRightNavButton => ToggleRightNavButtonElement;

    public System.Windows.Controls.Button UndoSelectedChangeButton => UndoSelectedChangeButtonElement;

    public System.Windows.Controls.TextBlock WebPreviewStatusText => WebPreviewStatusTextElement;

    public System.Windows.Controls.RowDefinition WorkbenchPanelRow => WorkbenchPanelRowElement;

    public System.Windows.Controls.ListBox WorkbenchProgressList => WorkbenchProgressListElement;

    private void WorkbenchProgressList_RequestBringIntoView(object sender, RequestBringIntoViewEventArgs e)
    {
        // Runtime events are a passive timeline. Item generation must not scroll
        // the entire operations rail away from its source-defined top state.
        e.Handled = true;
    }

    public void AnimateWorkbenchPanelReveal(bool collapsed)
    {
        var target = collapsed ? (FrameworkElement)WorkbenchRestoreBarElement : WorkbenchStatusPanelElement;
        target.BeginAnimation(OpacityProperty, null);
        target.RenderTransform = Transform.Identity;
        target.Opacity = 1;

        if (!SystemParameters.ClientAreaAnimation)
        {
            return;
        }

        var easing = new PowerEase { Power = 4, EasingMode = EasingMode.EaseOut };
        var translate = new TranslateTransform(0, collapsed ? -4 : -6);
        target.RenderTransform = translate;
        target.Opacity = 0;
        target.BeginAnimation(
            OpacityProperty,
            new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(180)) { EasingFunction = easing });
        translate.BeginAnimation(
            TranslateTransform.YProperty,
            new DoubleAnimation(translate.Y, 0, TimeSpan.FromMilliseconds(180)) { EasingFunction = easing });
    }

    public void AnimateWorkbenchLayout(
        double workbenchFrom,
        double workbenchTo,
        double splitterFrom,
        double splitterTo,
        double avatarFrom,
        double avatarTo,
        bool workbenchStarAfter,
        bool avatarStarAfter,
        Action<double, double, double, double>? frameUpdated = null,
        Action? completed = null)
    {
        StopWorkbenchLayoutAnimation(restoreAvatar: false);
        workbenchFrom = Math.Max(0, workbenchFrom);
        workbenchTo = Math.Max(0, workbenchTo);
        splitterFrom = Math.Max(0, splitterFrom);
        splitterTo = Math.Max(0, splitterTo);
        avatarFrom = Math.Max(1, avatarFrom);
        avatarTo = Math.Max(1, avatarTo);
        // Use pixel rows for the whole transition. WPF batches these invalidations
        // into the same composition pass, while every visible boundary follows the
        // same linear clock and lands on the exact terminal geometry.

        void Apply(double progress)
        {
            var frame = InterpolateWorkbenchLayout(
                workbenchFrom,
                workbenchTo,
                splitterFrom,
                splitterTo,
                avatarFrom,
                avatarTo,
                progress);
            WorkbenchPanelRow.Height = new GridLength(frame.Workbench, GridUnitType.Pixel);
            AvatarSplitterRow.Height = new GridLength(frame.Splitter, GridUnitType.Pixel);
            AvatarPanelRow.Height = new GridLength(frame.Avatar, GridUnitType.Pixel);
            frameUpdated?.Invoke(progress, frame.Workbench, frame.Splitter, frame.Avatar);
        }

        void Complete()
        {
            StopWorkbenchLayoutAnimation(restoreAvatar: false);
            WorkbenchPanelRow.Height = workbenchStarAfter
                ? new GridLength(1, GridUnitType.Star)
                : new GridLength(workbenchTo, GridUnitType.Pixel);
            AvatarSplitterRow.Height = new GridLength(splitterTo, GridUnitType.Pixel);
            AvatarPanelRow.Height = avatarStarAfter
                ? new GridLength(1, GridUnitType.Star)
                : new GridLength(avatarTo, GridUnitType.Pixel);
            InvalidateMeasure();
            InvalidateArrange();
            UpdateLayout();
            RestoreAvatarAfterLayoutAnimation();
            completed?.Invoke();
        }

        Apply(0);
        if (!SystemParameters.ClientAreaAnimation
            || (Math.Abs(workbenchFrom - workbenchTo) < 1
                && Math.Abs(splitterFrom - splitterTo) < 1
                && Math.Abs(avatarFrom - avatarTo) < 1))
        {
            Apply(1);
            Complete();
            return;
        }

        // The native WebView is hidden behind a captured proxy for this short interval,
        // so only WPF visuals resize. The HWND receives one final geometry update when
        // it is made visible in Complete().
        var clock = Stopwatch.StartNew();
        var durationMs = WorkbenchLayoutDurationMilliseconds(avatarFrom, avatarTo);
        var lastAppliedAtMs = -1000.0;
        EventHandler? tick = null;
        tick = (_, _) =>
        {
            var elapsedMs = clock.Elapsed.TotalMilliseconds;
            var progress = Math.Clamp(elapsedMs / durationMs, 0, 1);
            if (progress < 1 && elapsedMs - lastAppliedAtMs < 15.5)
            {
                return;
            }
            lastAppliedAtMs = elapsedMs;
            Apply(progress);
            if (progress >= 1)
            {
                Complete();
            }
        };
        _workbenchLayoutTick = tick;
        _workbenchLayoutTimer = new DispatcherTimer(DispatcherPriority.Render, Dispatcher)
        {
            Interval = TimeSpan.FromMilliseconds(16),
        };
        _workbenchLayoutTimer.Tick += tick;
        _workbenchLayoutTimer.Start();
    }

    internal static WorkbenchLayoutFrame InterpolateWorkbenchLayout(
        double workbenchFrom,
        double workbenchTo,
        double splitterFrom,
        double splitterTo,
        double avatarFrom,
        double avatarTo,
        double progress)
    {
        var linear = Math.Clamp(progress, 0, 1);
        return new WorkbenchLayoutFrame(
            workbenchFrom + (workbenchTo - workbenchFrom) * linear,
            splitterFrom + (splitterTo - splitterFrom) * linear,
            avatarFrom + (avatarTo - avatarFrom) * linear);
    }

    internal static double WorkbenchLayoutDurationMilliseconds(double avatarFrom, double avatarTo) =>
        Math.Clamp(220 + Math.Abs(avatarTo - avatarFrom) / 8, 240, 340);

    internal void ApplyWorkbenchPanelFinalGeometry()
    {
        WorkbenchPanelRow.MinHeight = 220;
        WorkbenchPanelRow.MaxHeight = double.PositiveInfinity;
        WorkbenchPanelRow.Height = new GridLength(1, GridUnitType.Star);
        AvatarSplitterRow.Height = new GridLength(0);
        AvatarPanelRow.MinHeight = 280;
        AvatarPanelRow.MaxHeight = 280;
        AvatarPanelRow.Height = new GridLength(280);
    }

    private void StopWorkbenchLayoutAnimation(bool restoreAvatar = true)
    {
        var tick = _workbenchLayoutTick;
        _workbenchLayoutTick = null;
        if (_workbenchLayoutTimer is not null)
        {
            if (tick is not null)
            {
                _workbenchLayoutTimer.Tick -= tick;
            }
            _workbenchLayoutTimer.Stop();
            _workbenchLayoutTimer = null;
        }
        if (restoreAvatar)
        {
            RestoreAvatarAfterLayoutAnimation();
        }
    }

    public System.Windows.Controls.Border WorkbenchStatusPanel => WorkbenchStatusPanelElement;

    public System.Windows.Controls.Border WorkbenchToolbar => WorkbenchToolbarElement;

    public System.Windows.Controls.RowDefinition WorkbenchToolbarRow => WorkbenchToolbarRowElement;

    public System.Windows.Controls.TextBlock WorkbenchWorkspaceHintText => WorkbenchWorkspaceHintTextElement;

    public System.Windows.Controls.TextBlock WorkbenchWorkspaceRootText => WorkbenchWorkspaceRootTextElement;

    private void StartAmbientMotion()
    {
        StopAmbientMotion();
        if (!SystemParameters.ClientAreaAnimation)
        {
            WorkbenchRunPulseElement.Visibility = Visibility.Collapsed;
            return;
        }

        WorkbenchRunPulseElement.Visibility = Visibility.Visible;
        var duration = TimeSpan.FromSeconds(4.6);
        var travel = Math.Max(26, WorkbenchProgressTrackElement.ActualHeight + 8);
        var movement = new DoubleAnimationUsingKeyFrames
        {
            Duration = new Duration(duration),
            RepeatBehavior = RepeatBehavior.Forever,
        };
        movement.KeyFrames.Add(new DiscreteDoubleKeyFrame(-8, KeyTime.FromTimeSpan(TimeSpan.Zero)));
        movement.KeyFrames.Add(new SplineDoubleKeyFrame(
            travel,
            KeyTime.FromTimeSpan(duration),
            new KeySpline(0.5, 0, 0.5, 1)));
        WorkbenchRunPulseTransformElement.BeginAnimation(
            TranslateTransform.YProperty,
            movement);

        var opacity = new DoubleAnimationUsingKeyFrames
        {
            Duration = new Duration(duration),
            RepeatBehavior = RepeatBehavior.Forever,
        };
        opacity.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(TimeSpan.Zero)));
        opacity.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(0.552))));
        opacity.KeyFrames.Add(new LinearDoubleKeyFrame(1, KeyTime.FromTimeSpan(TimeSpan.FromSeconds(3.772))));
        opacity.KeyFrames.Add(new LinearDoubleKeyFrame(0, KeyTime.FromTimeSpan(duration)));
        WorkbenchRunPulseElement.BeginAnimation(OpacityProperty, opacity);

    }

    private void StopAmbientMotion()
    {
        WorkbenchRunPulseElement.BeginAnimation(OpacityProperty, null);
        WorkbenchRunPulseTransformElement.BeginAnimation(TranslateTransform.YProperty, null);
    }

}

internal readonly record struct WorkbenchLayoutFrame(double Workbench, double Splitter, double Avatar);
