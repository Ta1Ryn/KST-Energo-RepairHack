using BackendFork.Domain;

namespace BackendFork.Infrastructure;

public static class DemoData
{
    public static DatabaseState Create()
    {
        var now = DateTimeOffset.UtcNow;
        return new DatabaseState
        {
            Equipment = [new Equipment
            {
                Id = Guid.Parse("10000000-0000-0000-0000-000000000001"),
                Name = "Демонстрационный насос", Model = "DEMO-PUMP", SerialNumber = "DEMO-001", QrCode = "FORK:DEMO-001",
                Characteristics = new() { ["Назначение"] = "Тестовые данные, заменить данными из БД заказчика" },
                Components =
                [
                    new Component
                    {
                        Id = Guid.Parse("20000000-0000-0000-0000-000000000001"), Name = "Подшипник", Type = "bearing",
                        NormLifetimeHours = 10000, ServiceIntervalHours = 1000,
                        Current = new Installation
                        {
                            Id = Guid.Parse("30000000-0000-0000-0000-000000000001"), InstalledAt = now.AddYears(-1),
                            OperatingHours = 9700, HoursMeasuredAt = now, LastServiceAt = now.AddDays(-40), HoursAtLastService = 8600
                        },
                        Sensors = [new Sensor { Key = "temperature", Name = "Температура", Unit = "°C", Min = 0, Max = 80, Value = 88, MeasuredAt = now }]
                    },
                    new Component
                    {
                        Id = Guid.Parse("20000000-0000-0000-0000-000000000002"), Name = "Уплотнение", Type = "seal",
                        NormLifetimeHours = 5000, ServiceIntervalHours = 500,
                        Current = new Installation
                        {
                            Id = Guid.Parse("30000000-0000-0000-0000-000000000002"), InstalledAt = now.AddMonths(-2), OperatingHours = null
                        },
                        Sensors = [new Sensor { Key = "pressure", Name = "Давление", Unit = "bar", Min = 1, Max = 6 }]
                    }
                ]
            }]
        };
    }
}
