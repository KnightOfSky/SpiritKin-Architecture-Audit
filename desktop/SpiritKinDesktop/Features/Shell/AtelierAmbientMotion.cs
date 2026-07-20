using System;
using System.Windows;
using System.Windows.Media.Animation;

namespace SpiritKinDesktop;

public partial class MainWindow
{
    private void StartAtelierAmbientMotion()
    {
        StopAtelierAmbientMotion();

        if (!SystemParameters.ClientAreaAnimation)
        {
            AtelierGlassDriftTransformElement.X = 0;
            AtelierGlassDriftElement.Opacity = 0.28;
            SetLightFlowStatic(ThemeManager.CurrentIsDark);
            return;
        }

        var isDark = ThemeManager.CurrentIsDark;
        var duration = TimeSpan.FromSeconds(isDark ? 12 : 40);
        var driftEase = new SineEase { EasingMode = EasingMode.EaseInOut };
        AtelierGlassDriftElement.Visibility = isDark ? Visibility.Visible : Visibility.Collapsed;
        AtelierLightFlowPrimaryElement.Visibility = Visibility.Visible;
        AtelierLightFlowWarningElement.Visibility = isDark ? Visibility.Visible : Visibility.Collapsed;
        AtelierLightFlowSecondaryElement.Visibility = isDark ? Visibility.Visible : Visibility.Collapsed;
        AtelierLightFlowPrimaryElement.Opacity = isDark ? 0.35 : 0.65;
        AtelierLightFlowWarningElement.Opacity = 0.35;
        AtelierLightFlowSecondaryElement.Opacity = 0.35;
        if (isDark)
        {
            AtelierGlassDriftTransformElement.BeginAnimation(
                System.Windows.Media.TranslateTransform.XProperty,
                new DoubleAnimation(-18, 18, duration)
                {
                    AutoReverse = true,
                    RepeatBehavior = RepeatBehavior.Forever,
                    EasingFunction = driftEase,
                });
            AtelierGlassDriftElement.BeginAnimation(
                OpacityProperty,
                new DoubleAnimation(0.22, 0.35, duration)
                {
                    AutoReverse = true,
                    RepeatBehavior = RepeatBehavior.Forever,
                    EasingFunction = driftEase,
                });
        }

        var width = Math.Max(ActualWidth, 1);
        // desktop-priority.css lightFlow:
        // primary 180% -> -80%, warning -80% -> 180%, secondary 130% -> -80%.
        StartLinearSweep(AtelierLightFlowPrimaryTransformElement, (isDark ? 1.56 : 1.30) * width, (isDark ? -1.56 : -1.30) * width, duration);
        if (isDark)
        {
            StartLinearSweep(AtelierLightFlowWarningTransformElement, -1.04 * width, 1.04 * width, duration);
            StartLinearSweep(AtelierLightFlowSecondaryTransformElement, 0.78 * width, -0.48 * width, duration);
        }
    }

    private void StopAtelierAmbientMotion()
    {
        AtelierGlassDriftTransformElement.BeginAnimation(
            System.Windows.Media.TranslateTransform.XProperty,
            null);
        AtelierGlassDriftElement.BeginAnimation(OpacityProperty, null);
        AtelierLightFlowPrimaryTransformElement.BeginAnimation(
            System.Windows.Media.TranslateTransform.XProperty,
            null);
        AtelierLightFlowWarningTransformElement.BeginAnimation(
            System.Windows.Media.TranslateTransform.XProperty,
            null);
        AtelierLightFlowSecondaryTransformElement.BeginAnimation(
            System.Windows.Media.TranslateTransform.XProperty,
            null);
    }

    private static void StartLinearSweep(
        System.Windows.Media.TranslateTransform transform,
        double from,
        double to,
        TimeSpan duration)
    {
        transform.BeginAnimation(
            System.Windows.Media.TranslateTransform.XProperty,
            new DoubleAnimation(from, to, duration)
            {
                RepeatBehavior = RepeatBehavior.Forever,
            });
    }

    private void SetLightFlowStatic(bool isDark)
    {
        AtelierGlassDriftElement.Visibility = isDark ? Visibility.Visible : Visibility.Collapsed;
        AtelierLightFlowPrimaryElement.Visibility = Visibility.Visible;
        AtelierLightFlowWarningElement.Visibility = isDark ? Visibility.Visible : Visibility.Collapsed;
        AtelierLightFlowSecondaryElement.Visibility = isDark ? Visibility.Visible : Visibility.Collapsed;
        AtelierLightFlowPrimaryTransformElement.X = 0;
        AtelierLightFlowWarningTransformElement.X = 0;
        AtelierLightFlowSecondaryTransformElement.X = 0;
        AtelierLightFlowPrimaryElement.Opacity = isDark ? 0.35 : 0.65;
        AtelierLightFlowWarningElement.Opacity = 0.35;
        AtelierLightFlowSecondaryElement.Opacity = 0.35;
    }
}
