namespace SpiritKinDesktop.Tests;

public sealed class DesktopContractSmokeTests
{
    [Fact]
    public void RealtimeContractExposesStableDesktopPorts()
    {
        Assert.Equal("spiritkin.realtime_contract.v1", RealtimeContract.SchemaVersion);
        Assert.Equal(8787, RealtimeContract.DefaultPorts.Frontend);
        Assert.Equal(8765, RealtimeContract.DefaultPorts.EventBridge);
        Assert.Equal(8788, RealtimeContract.DefaultPorts.CommandGateway);
        Assert.Equal(8790, RealtimeContract.DefaultPorts.RemoteWorker);
    }

    [Fact]
    public void DesktopStateDefaultNormalizesActiveSession()
    {
        var state = DesktopState.CreateDefault().Normalized();

        Assert.NotNull(state);
        Assert.NotEmpty(state.Sessions);
        Assert.Contains(state.Sessions, session => session.Id == state.ActiveSessionId);
        Assert.NotNull(state.QuickCommands);
        Assert.NotNull(state.Events);
        Assert.NotNull(state.Settings);
    }
}
