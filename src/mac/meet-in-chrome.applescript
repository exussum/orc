on open location theURL
	if theURL starts with "orc-chrome://" then
		set theLink to "https://" & text 14 thru -1 of theURL
		do shell script "open -a 'Google Chrome' " & quoted form of theLink
	end if
end open location
