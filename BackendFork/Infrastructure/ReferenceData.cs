using BackendFork.Domain;

namespace BackendFork.Infrastructure;

public static class ReferenceData
{
    public static void Upgrade(DatabaseState state, bool seedDemo)
    {
        foreach (var equipment in state.Equipment)
        {
            equipment.Nameplate.Model ??= equipment.Model;
            equipment.Nameplate.SerialNumber ??= equipment.SerialNumber;
            foreach (var component in equipment.Components)
            foreach (var sensor in component.Sensors)
            {
                if (string.IsNullOrWhiteSpace(sensor.Kind)) sensor.Kind = sensor.Key;
                if (sensor.NormId is not null || (sensor.Min is null && sensor.Max is null)) continue;
                var norm = new SensorNorm
                {
                    Name = component.Name + ": " + sensor.Name, SensorKind = sensor.Kind, Unit = sensor.Unit,
                    Min = sensor.Min, Max = sensor.Max, EquipmentModel = equipment.Model, ComponentType = component.Type,
                    IsDemo = equipment.Model.StartsWith("DEMO",StringComparison.OrdinalIgnoreCase)
                };
                state.SensorNorms.Add(norm); sensor.NormId = norm.Id;
            }
        }
        if (!seedDemo || state.Instructions.Count > 0) return;
        foreach (var kind in new[]{"temperature","vibration","pressure"})
        {
            var instruction = new Instruction { Title = "Тестовая инструкция: " + kind, IsDemo = true,
                Text = "Демонстрационная рекомендация: проверить показание датчика и провести осмотр узла по утверждённому регламенту." };
            state.Instructions.Add(instruction);
            state.RecommendationRules.Add(new RecommendationRule { Trigger = RecommendationTrigger.SensorOutOfRange,
                SensorKind = kind, InstructionId = instruction.Id, Message = "Показание выше или ниже нормы. Проверить датчик и узел." });
        }
        foreach (var defect in DefectCatalog.All)
        {
            var instruction = new Instruction { Title = "Тестовая инструкция: " + defect.Name, IsDemo = true,
                Text = "Демонстрационная рекомендация: зафиксировать видимый признак на фото и провести проверку по регламенту оборудования." };
            state.Instructions.Add(instruction);
            state.RecommendationRules.Add(new RecommendationRule { Trigger = RecommendationTrigger.VisualDefect,
                DefectCode = defect.Code, InstructionId = instruction.Id, Message = "Обнаружен визуальный признак: " + defect.Name });
        }
    }
}
