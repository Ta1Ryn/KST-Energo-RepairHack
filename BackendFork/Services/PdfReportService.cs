using System.Buffers.Binary;
using System.Globalization;
using System.Text;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using Microsoft.Extensions.Options;

namespace BackendFork.Services;

public sealed class PdfReportService(IEquipmentRepository repository,IOptions<ReportOptions> options)
{
    public Task<Inspection> GetAsync(Guid equipmentId,Guid inspectionId,CancellationToken ct)=>repository.ReadAsync(s=>
    {
        EquipmentService.FindEquipment(s,equipmentId);
        return s.Inspections.Find(i=>i.Id==inspectionId&&i.EquipmentId==equipmentId)??throw new ApiException(404,"Отчёт не найден.");
    },ct);
    public async Task<byte[]> BuildAsync(Guid equipmentId,Guid inspectionId,CancellationToken ct)
    {
        var report=await GetAsync(equipmentId,inspectionId,ct);
        ApiException.Require(File.Exists(options.Value.FontPath),"Для PDF укажите Reports:FontPath — путь к Unicode-шрифту TTF.",503);
        var font=new TrueTypeFont(await File.ReadAllBytesAsync(options.Value.FontPath,ct));
        var text=new List<string>
        {
            "ОТЧЁТ ОСМОТРА ОБОРУДОВАНИЯ", "Номер: "+report.Id,
            "Дата осмотра: "+report.PerformedAt.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss 'UTC'",CultureInfo.InvariantCulture),
            "Инженер: "+report.AuthorName+" ("+report.AuthorId+")"
        };
        var snapshot=report.EquipmentSnapshot;
        if(snapshot is not null)
        {
            text.Add("Оборудование: "+snapshot.Name);text.Add("Модель: "+snapshot.Model);
            text.Add("Серийный номер: "+snapshot.SerialNumber);
        }
        else text.Add("Исторический снимок показаний отсутствует: отчёт создан до добавления этой функции.");
        if(!string.IsNullOrWhiteSpace(report.Comment))text.Add("Замечание: "+report.Comment);
        foreach(var item in report.Items)
        {
            var component=snapshot?.Components.FirstOrDefault(c=>c.Id==item.ComponentId);
            text.Add("");text.Add("Узел: "+(component?.Name??item.ComponentId.ToString()));
            text.Add("Установленный экземпляр: "+item.InstallationId);
            text.Add("Работа: "+(item.Action switch { WorkAction.Checked=>"Проверка",WorkAction.DefectRecorded=>"Регистрация дефекта",
                WorkAction.Serviced=>"Обслуживание",WorkAction.Replaced=>"Замена",_=>item.Action.ToString() }));
            if(item.NewInstallationId is{} replacement)text.Add("Новый экземпляр: "+replacement);
            if(item.Comment is not null)text.Add("Замечание: "+item.Comment);
            if(item.DefectResolved)text.Add("Инженер подтвердил устранение дефекта.");
            if(component is not null)
            {
                text.Add("Наработка: "+Value(component.Resource.OperatingHours)+" ч.");
                text.Add("Нормативный ресурс: "+Value(component.Resource.NormLifetimeHours)+" ч.; остаток: "+Value(component.Resource.RemainingHours)+" ч.");
                foreach(var limit in component.Resource.Limitations)text.Add("Ограничение: "+limit);
                foreach(var sensor in component.Sensors)
                    text.Add($"Датчик {sensor.Name}: {Value(sensor.Value)} {sensor.Unit}; норма {Value(sensor.Min)}–{Value(sensor.Max)}; статус {sensor.Status}; норма {sensor.NormId}, версия {sensor.NormRevision}; измерено {sensor.MeasuredAt:O}");
                if(component.LatestVisualAnalysis is{} visual)
                    text.Add($"Визуальный анализ: {visual.VisualStatus}. {visual.Summary} Рекомендация: {visual.Recommendation}");
                foreach(var recommendation in component.Recommendations??[])
                    text.Add("Рекомендация: "+recommendation.Message+" Причина: "+recommendation.Reason+
                        (recommendation.IsDemo?" [ТЕСТОВЫЕ ДАННЫЕ]":"")+
                        (recommendation.InstructionId is{} id?" Инструкция: "+id+", версия "+recommendation.InstructionRevision:""));
            }
            if(item.PhotoIds.Count>0)text.Add("Фото в системе: "+string.Join(", ",item.PhotoIds));
            if(item.AnalysisId is{} analysis)text.Add("Связанный анализ: "+analysis);
        }
        text.Add("");text.Add("Осмотр и расчёт ресурса не подтверждают отсутствие скрытых неисправностей.");
        return PdfWriter.Build(text,font);
    }
    private static string Value(decimal? value)=>value?.ToString(CultureInfo.InvariantCulture)??"нет данных";

    private sealed class TrueTypeFont
    {
        public byte[] Bytes { get; }
        private readonly Dictionary<string,int> tables=[];
        private readonly int cmap,hmtx,metricCount,units;
        public TrueTypeFont(byte[] bytes)
        {
            Bytes=bytes;
            if(bytes.Length<12)throw new InvalidDataException("Invalid TTF font.");
            for(var i=0;i<U16(4);i++){var pos=12+i*16;tables[Encoding.ASCII.GetString(bytes,pos,4)]=checked((int)U32(pos+8));}
            units=U16(tables["head"]+18);hmtx=tables["hmtx"];metricCount=U16(tables["hhea"]+34);
            var root=tables["cmap"];var selected=0;var rank=0;
            for(var i=0;i<U16(root+2);i++)
            {
                var record=root+4+i*8;var offset=root+checked((int)U32(record+4));
                var format=U16(offset);var candidate=format==12?2:format==4?1:0;
                if(candidate>rank){rank=candidate;selected=offset;}
            }
            if(rank==0)throw new InvalidDataException("Font must have a Unicode cmap.");
            cmap=selected;
        }
        private ushort U16(int p)=>BinaryPrimitives.ReadUInt16BigEndian(Bytes.AsSpan(p,2));
        private uint U32(int p)=>BinaryPrimitives.ReadUInt32BigEndian(Bytes.AsSpan(p,4));
        public ushort Glyph(char ch)
        {
            if(U16(cmap)==12)
            {
                var count=U32(cmap+12);
                for(var i=0;i<count;i++)
                {
                    var p=cmap+16+(int)i*12;var start=U32(p);var end=U32(p+4);
                    if(ch>=start&&ch<=end)return checked((ushort)(U32(p+8)+ch-start));
                }
                return 0;
            }
            var segments=U16(cmap+6)/2;var endCodes=cmap+14;var starts=endCodes+segments*2+2;
            var deltas=starts+segments*2;var ranges=deltas+segments*2;
            for(var i=0;i<segments;i++)
            {
                if(ch>U16(endCodes+i*2)||ch<U16(starts+i*2))continue;
                var delta=U16(deltas+i*2);var range=U16(ranges+i*2);
                if(range==0)return unchecked((ushort)(ch+delta));
                var glyph=U16(ranges+i*2+range+(ch-U16(starts+i*2))*2);
                return glyph==0?(ushort)0:unchecked((ushort)(glyph+delta));
            }
            return 0;
        }
        public int Width(char ch)
        {
            var glyph=Glyph(ch);var metric=Math.Min(glyph,metricCount-1);
            return (int)Math.Round(U16(hmtx+metric*4)*1000.0/units);
        }
        public IEnumerable<string> Wrap(string text)
        {
            foreach(var paragraph in text.Replace("\r","").Split('\n'))
            {
                var clean=new string(paragraph.Select(c=>char.IsSurrogate(c)||char.IsControl(c)?' ':c).ToArray());
                if(clean.Length==0){yield return "";continue;}
                var start=0;
                while(start<clean.Length)
                {
                    var end=start;var width=0;var lastSpace=-1;
                    while(end<clean.Length&&width+Width(clean[end])<=44000){width+=Width(clean[end]);if(clean[end]==' ')lastSpace=end;end++;}
                    if(end==start)end++;
                    if(end<clean.Length&&lastSpace>start)end=lastSpace;
                    yield return clean[start..end].Trim();
                    start=end;while(start<clean.Length&&clean[start]==' ')start++;
                }
            }
        }
    }
    private static class PdfWriter
    {
        public static byte[] Build(List<string> text,TrueTypeFont font)
        {
            var lines=text.SelectMany(font.Wrap).ToList();
            var chars=lines.SelectMany(x=>x).Distinct().OrderBy(c=>c).ToList();
            var objects=new List<byte[]>();
            int Add(byte[] bytes){objects.Add(bytes);return objects.Count;}
            void Set(int id,string value)=>objects[id-1]=Encoding.ASCII.GetBytes(value);
            var catalog=Add([]);var pages=Add([]);var fontType=Add([]);var cidFont=Add([]);var descriptor=Add([]);
            var fontFile=Add(Stream(font.Bytes," /Length1 "+font.Bytes.Length));
            var mapping=new byte[(chars.Count==0?1:chars.Max()+1)*2];
            foreach(var ch in chars)BinaryPrimitives.WriteUInt16BigEndian(mapping.AsSpan(ch*2,2),font.Glyph(ch));
            var cidMap=Add(Stream(mapping,""));
            var cmap=new StringBuilder("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n");
            foreach(var chunk in chars.Chunk(100))
            {
                cmap.Append(chunk.Length).Append(" beginbfchar\n");
                foreach(var ch in chunk)cmap.Append('<').Append(((int)ch).ToString("X4")).Append("> <").Append(((int)ch).ToString("X4")).Append(">\n");
                cmap.Append("endbfchar\n");
            }
            cmap.Append("endcmap CMapName currentdict /CMap defineresource pop end end");
            var unicode=Add(Stream(Encoding.ASCII.GetBytes(cmap.ToString()),""));
            Set(fontType,$"<< /Type /Font /Subtype /Type0 /BaseFont /Arial /Encoding /Identity-H /DescendantFonts [{cidFont} 0 R] /ToUnicode {unicode} 0 R >>");
            var widths=string.Join(" ",chars.Select(ch=>$"{(int)ch} [{font.Width(ch)}]"));
            Set(cidFont,$"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Arial /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor {descriptor} 0 R /CIDToGIDMap {cidMap} 0 R /DW 500 /W [{widths}] >>");
            Set(descriptor,$"<< /Type /FontDescriptor /FontName /Arial /Flags 32 /FontBBox [-1000 -1000 3000 3000] /ItalicAngle 0 /Ascent 905 /Descent -212 /CapHeight 716 /StemV 80 /FontFile2 {fontFile} 0 R >>");
            var pageIds=new List<int>();
            foreach(var chunk in lines.Chunk(43))
            {
                var content=new StringBuilder("BT /F1 11 Tf 16 TL 50 792 Td\n");
                foreach(var line in chunk)content.Append('<').Append(Convert.ToHexString(Encoding.BigEndianUnicode.GetBytes(line))).Append("> Tj T*\n");
                content.Append("ET");
                var contentId=Add(Stream(Encoding.ASCII.GetBytes(content.ToString()),""));
                var page=Add([]);Set(page,$"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {fontType} 0 R >> >> /Contents {contentId} 0 R >>");pageIds.Add(page);
            }
            Set(catalog,$"<< /Type /Catalog /Pages {pages} 0 R >>");
            Set(pages,$"<< /Type /Pages /Kids [{string.Join(" ",pageIds.Select(id=>$"{id} 0 R"))}] /Count {pageIds.Count} >>");
            using var output=new MemoryStream();
            void Write(string value)=>output.Write(Encoding.ASCII.GetBytes(value));
            Write("%PDF-1.7\n");var offsets=new List<long>{0};
            for(var i=0;i<objects.Count;i++){offsets.Add(output.Position);Write($"{i+1} 0 obj\n");output.Write(objects[i]);Write("\nendobj\n");}
            var xref=output.Position;Write($"xref\n0 {objects.Count+1}\n0000000000 65535 f \n");
            foreach(var offset in offsets.Skip(1))Write(offset.ToString("D10",CultureInfo.InvariantCulture)+" 00000 n \n");
            Write($"trailer\n<< /Size {objects.Count+1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n");
            return output.ToArray();
        }
        private static byte[] Stream(byte[] body,string properties)
        {
            using var output=new MemoryStream();output.Write(Encoding.ASCII.GetBytes($"<< /Length {body.Length}{properties} >>\nstream\n"));
            output.Write(body);output.Write(Encoding.ASCII.GetBytes("\nendstream"));return output.ToArray();
        }
    }
}
